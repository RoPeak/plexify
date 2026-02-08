from __future__ import annotations

from pathlib import Path

import requests

from plexify import cli
from plexify.cache import Cache
from plexify.tv_episode_cache import EpisodeCache
from plexify.util import movie_cache_key, tv_show_folder_cache_key
from plexify.infer import InferredItem
from plexify.sources import tvmaze


def test_plan_items_advances_progress(monkeypatch, tmp_path: Path) -> None:
    class ProgressStub:
        last = None

        def __init__(self, *args, **kwargs) -> None:
            self.added: list[tuple[str, int]] = []
            self.updated: list[dict[str, str]] = []
            self.advanced: list[int] = []
            self.live = False
            ProgressStub.last = self

        def __enter__(self) -> "ProgressStub":
            return self

        def __exit__(self, *_exc) -> None:
            return None

        def add_task(self, description: str, total: int) -> str:
            self.added.append((description, total))
            return "task"

        def update(self, _task: str, **kwargs) -> None:
            self.updated.append(kwargs)

        def advance(self, _task: str, advance: int = 1) -> None:
            self.advanced.append(advance)

        def stop(self) -> None:
            self.live = False

        def start(self) -> None:
            self.live = True

    def _noop_process(*_args, **_kwargs):
        return None

    monkeypatch.setattr(cli, "Progress", ProgressStub)
    monkeypatch.setattr(cli, "_process_item", _noop_process)

    incoming = tmp_path / "incoming"
    library = tmp_path / "library"
    incoming.mkdir()
    library.mkdir()
    (incoming / "One.mkv").write_text("x", encoding="utf-8")
    (incoming / "Two.mp4").write_text("x", encoding="utf-8")

    cli._plan_items(
        incoming=incoming,
        library=library,
        mode="dry-run",
        copy_mode=True,
        interactive=False,
        auto_accept=False,
        min_confidence=0.55,
        extensions=cli.DEFAULT_EXTENSIONS,
        cache_path=library / ".plexify" / "cache.json",
        limit=None,
        show_cache=False,
        media_type_filter=None,
        use_cache=True,
        on_conflict="rename",
    )

    progress = ProgressStub.last
    assert progress is not None
    descriptions = [entry.get("description", "") for entry in progress.updated]
    assert any("One.mkv" in desc for desc in descriptions)
    assert any("Two.mp4" in desc for desc in descriptions)
    completed = [entry.get("completed") for entry in progress.updated if "completed" in entry]
    assert completed
    assert max(value for value in completed if value is not None) <= 2


def test_plan_items_progress_rewinds_on_back(monkeypatch, tmp_path: Path) -> None:
    class ProgressStub:
        last = None

        def __init__(self, *args, **kwargs) -> None:
            self.updated: list[dict[str, int | str]] = []
            ProgressStub.last = self

        def __enter__(self) -> "ProgressStub":
            return self

        def __exit__(self, *_exc) -> None:
            return None

        def add_task(self, description: str, total: int) -> str:
            return "task"

        def update(self, _task: str, **kwargs) -> None:
            self.updated.append(kwargs)

    calls = {"count": 0}

    def _process(*_args, **_kwargs):
        calls["count"] += 1
        if calls["count"] == 2:
            raise cli.BackRequested
        return None, False

    monkeypatch.setattr(cli, "Progress", ProgressStub)
    monkeypatch.setattr(cli, "_process_item", _process)

    incoming = tmp_path / "incoming"
    library = tmp_path / "library"
    incoming.mkdir()
    library.mkdir()
    (incoming / "One.mkv").write_text("x", encoding="utf-8")
    (incoming / "Two.mp4").write_text("x", encoding="utf-8")

    cli._plan_items(
        incoming=incoming,
        library=library,
        mode="dry-run",
        copy_mode=True,
        interactive=False,
        auto_accept=False,
        min_confidence=0.55,
        extensions=cli.DEFAULT_EXTENSIONS,
        cache_path=library / ".plexify" / "cache.json",
        limit=None,
        show_cache=False,
        media_type_filter=None,
        use_cache=True,
        on_conflict="rename",
    )

    progress = ProgressStub.last
    assert progress is not None
    completed = [entry.get("completed") for entry in progress.updated if "completed" in entry]
    assert completed
    assert max(value for value in completed if value is not None) <= 2


def test_interactive_enrichment_uses_stricter_timeouts(monkeypatch, tmp_path: Path) -> None:
    timeouts: list[tuple[int, int]] = []

    def _fake_enrichment(*_args, **kwargs):
        timeouts.append(kwargs.get("timeout"))
        return {"director": "Someone"}

    def _fake_show_details(*_args, **kwargs):
        timeouts.append(kwargs.get("timeout"))
        return tvmaze.TVMazeShowDetails(network="Net", creator=None, cast=["A"])

    monkeypatch.setattr(cli.wikidata, "fetch_enrichment", _fake_enrichment)
    monkeypatch.setattr(cli.tvmaze, "fetch_show_details", _fake_show_details)

    cache = Cache(tmp_path / "cache.json")
    movie = cli.Candidate(
        title="Film",
        year=2000,
        source="Wikidata",
        confidence=0.8,
        metadata={"qid": "Q1"},
        enrichment=None,
    )
    tv = cli.Candidate(
        title="Show",
        year=2020,
        source="TVMaze",
        confidence=0.9,
        metadata={"id": 123},
        enrichment=None,
    )

    cli._maybe_enrich_candidates(
        "movie",
        [movie],
        session_tv=requests.Session(),
        session_wd=requests.Session(),
        cache=cache,
        interactive=True,
    )
    cli._maybe_enrich_candidates(
        "tv",
        [tv],
        session_tv=requests.Session(),
        session_wd=requests.Session(),
        cache=cache,
        interactive=True,
    )

    assert timeouts == [(2, 5), (2, 5)]


def test_tv_episode_fetch_called_once_for_selected_candidate(monkeypatch, tmp_path: Path) -> None:
    calls = {"count": 0}

    def _fake_fetch_episodes(*_args, **_kwargs):
        calls["count"] += 1
        return [tvmaze.TVMazeEpisode(season=1, number=2, name="Pilot")]

    def _fake_tv_candidates(*_args, **_kwargs) -> cli.CandidatePage:
        candidate = cli.Candidate(
            title="Show",
            year=2020,
            source="TVMaze",
            confidence=0.95,
            metadata={"id": 99, "name": "Show", "year": 2020},
            enrichment=None,
        )
        return cli.CandidatePage(candidates=[candidate], raw_results=None, next_offset=0, has_more=False)

    monkeypatch.setattr(cli.tvmaze, "fetch_episodes", _fake_fetch_episodes)
    monkeypatch.setattr(cli, "_tv_candidates", _fake_tv_candidates)

    incoming = tmp_path / "incoming"
    library = tmp_path / "library"
    incoming.mkdir()
    library.mkdir()
    path = incoming / "Show.S01E02.mkv"
    path.write_text("x", encoding="utf-8")

    item = InferredItem(path=path, media_type="tv", title="Show", year=None, season=1, episode=2, episode_title=None)
    cache = Cache(library / ".plexify" / "cache.json")

    plan, _collision = cli._process_item(
        item=item,
        library=library,
        cache=cache,
        mode="dry-run",
        copy_mode=True,
        interactive=False,
        auto_accept=True,
        min_confidence=0.55,
        session_tv=requests.Session(),
        session_wd=requests.Session(),
        episode_cache=EpisodeCache(),
        progress=None,
        show_cache=False,
        incoming_root=incoming,
        planned={},
        on_conflict="rename",
    )

    assert plan is not None
    assert calls["count"] == 1
    assert plan.metadata.get("episode_title") == "Pilot"


def test_reusable_movie_cache_key_hit(tmp_path: Path) -> None:
    cache = Cache(tmp_path / "cache.json")
    item = InferredItem(
        path=tmp_path / "Movie" / "Superman II.mkv",
        media_type="movie",
        title="Superman II",
        year=1980,
        episode_title=None,
    )
    cache.set_movie(
        movie_cache_key(item.title, item.year),
        {"qid": "Q1", "title": "Superman II", "year": 1980, "confirmed_by_user": True, "manual": False},
    )
    page = cli._movie_candidates(
        item,
        session=requests.Session(),
        cache=cache,
        show_cache=False,
        cache_key="movie|path|superman ii|1980",
    )
    assert page.cache_hit is True
    assert page.candidates[0].title == "Superman II"


def test_reusable_movie_cache_ignored_when_stem_has_extra_tokens(monkeypatch, tmp_path: Path) -> None:
    def _fake_search(*_args, **_kwargs):
        return []

    monkeypatch.setattr(cli.wikidata, "search", _fake_search)

    cache = Cache(tmp_path / "cache.json")
    cache.set_movie(
        movie_cache_key("Twilight", None),
        {"qid": "Q1", "title": "Twilight", "year": 2008, "confirmed_by_user": True, "manual": False},
    )
    item = InferredItem(
        path=tmp_path / "Twilight - 2 - New Moon.avi",
        media_type="movie",
        title="Twilight",
        year=None,
        episode_title=None,
    )
    page = cli._movie_candidates(
        item,
        session=requests.Session(),
        cache=cache,
        show_cache=False,
        cache_key="movie|path|twilight-2-new-moon|unknown",
    )
    assert page.cache_hit is False


def test_reusable_movie_cache_disabled_without_year(monkeypatch, tmp_path: Path) -> None:
    def _fake_search(*_args, **_kwargs):
        return []

    monkeypatch.setattr(cli.wikidata, "search", _fake_search)

    cache = Cache(tmp_path / "cache.json")
    cache.set_movie(
        movie_cache_key("Twilight", None),
        {"qid": "Q1", "title": "Twilight", "year": 2008, "confirmed_by_user": True, "manual": False},
    )
    item = InferredItem(
        path=tmp_path / "Twilight.mkv",
        media_type="movie",
        title="Twilight",
        year=None,
        episode_title=None,
    )
    page = cli._movie_candidates(
        item,
        session=requests.Session(),
        cache=cache,
        show_cache=False,
        cache_key="movie|path|twilight|unknown",
    )
    assert page.cache_hit is False


def test_reusable_movie_cache_enabled_without_year_for_unambiguous_title(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli.wikidata, "search", lambda *_args, **_kwargs: [])

    cache = Cache(tmp_path / "cache.json")
    cache.set_movie(
        movie_cache_key("The Dark Knight", None),
        {"qid": "Q1", "title": "The Dark Knight", "year": 2008, "confirmed_by_user": True, "manual": False},
    )
    item = InferredItem(
        path=tmp_path / "The Dark Knight.mkv",
        media_type="movie",
        title="The Dark Knight",
        year=None,
        episode_title=None,
    )
    page = cli._movie_candidates(
        item,
        session=requests.Session(),
        cache=cache,
        show_cache=False,
        cache_key="movie|path|the-dark-knight|unknown",
    )
    assert page.cache_hit is True


def test_reusable_tv_cache_not_written_without_year(monkeypatch, tmp_path: Path) -> None:
    def _fake_tv_candidates(*_args, **_kwargs) -> cli.CandidatePage:
        candidate = cli.Candidate(
            title="Show",
            year=2010,
            source="TVMaze",
            confidence=1.0,
            metadata={"id": 123, "name": "Show", "year": 2010},
            enrichment=None,
        )
        return cli.CandidatePage(candidates=[candidate], raw_results=None, next_offset=0, has_more=False)

    monkeypatch.setattr(cli, "_tv_candidates", _fake_tv_candidates)
    monkeypatch.setattr(cli.tvmaze, "fetch_episodes", lambda *_args, **_kwargs: [])

    incoming = tmp_path / "incoming"
    library = tmp_path / "library"
    incoming.mkdir()
    library.mkdir()
    path = incoming / "Show.S01E02.mkv"
    path.write_text("x", encoding="utf-8")

    item = InferredItem(path=path, media_type="tv", title="Show", year=None, season=1, episode=2, episode_title=None)
    cache = Cache(library / ".plexify" / "cache.json")

    plan, _collision = cli._process_item(
        item=item,
        library=library,
        cache=cache,
        mode="dry-run",
        copy_mode=True,
        interactive=False,
        auto_accept=True,
        min_confidence=0.55,
        session_tv=requests.Session(),
        session_wd=requests.Session(),
        episode_cache=EpisodeCache(),
        progress=None,
        show_cache=False,
        incoming_root=incoming,
        planned={},
        on_conflict="rename",
    )

    assert plan is not None
    cache_key = cli.build_cache_key(path, incoming, "tv", None)
    assert cache.get_show(cache_key) is not None
    assert cache.get_show(cli.tv_show_cache_key(item.title, item.year)) is None
    folder_key = tv_show_folder_cache_key(path, incoming)
    assert folder_key is None


def test_prompt_season_episode_reprompts_on_invalid_number(monkeypatch, tmp_path: Path) -> None:
    def _fake_tv_candidates(*_args, **_kwargs) -> cli.CandidatePage:
        candidate = cli.Candidate(
            title="Show",
            year=2010,
            source="TVMaze",
            confidence=1.0,
            metadata={"id": 123, "name": "Show", "year": 2010},
            enrichment=None,
        )
        return cli.CandidatePage(candidates=[candidate], raw_results=None, next_offset=0, has_more=False)

    answers = iter(["abc", "2", "def", "3", "Chosen Title"])
    messages: list[str] = []
    monkeypatch.setattr(cli, "_tv_candidates", _fake_tv_candidates)
    monkeypatch.setattr(cli, "_prompt_text", lambda *_args, **_kwargs: next(answers))
    monkeypatch.setattr(cli, "_safe_print", lambda message, _progress=None: messages.append(str(message)))
    monkeypatch.setattr(cli.tvmaze, "fetch_episodes", lambda *_args, **_kwargs: [])

    incoming = tmp_path / "incoming"
    library = tmp_path / "library"
    incoming.mkdir()
    library.mkdir()
    path = incoming / "Show" / "Season X" / "Episode.avi"
    path.parent.mkdir(parents=True)
    path.write_text("x", encoding="utf-8")

    item = InferredItem(path=path, media_type="tv", title="Show", year=None, season=None, episode=None, episode_title=None)
    cache = Cache(library / ".plexify" / "cache.json")

    plan, _collision = cli._process_item(
        item=item,
        library=library,
        cache=cache,
        mode="dry-run",
        copy_mode=True,
        interactive=True,
        auto_accept=True,
        min_confidence=0.55,
        session_tv=requests.Session(),
        session_wd=requests.Session(),
        episode_cache=EpisodeCache(),
        progress=None,
        show_cache=False,
        incoming_root=incoming,
        planned={},
        on_conflict="rename",
    )

    assert plan is not None
    assert plan.metadata["season"] == 2
    assert plan.metadata["episode"] == 3
    assert messages.count("Please enter a whole number.") == 2


def test_process_item_tv_saves_cache_once_per_item(monkeypatch, tmp_path: Path) -> None:
    class CacheSpy(Cache):
        def __init__(self, path: Path) -> None:
            super().__init__(path)
            self.save_calls = 0

        def save(self) -> None:
            self.save_calls += 1
            super().save()

    def _fake_tv_candidates(*_args, **_kwargs) -> cli.CandidatePage:
        candidate = cli.Candidate(
            title="Show",
            year=2010,
            source="TVMaze",
            confidence=1.0,
            metadata={"id": 123, "name": "Show", "year": 2010},
            enrichment=None,
        )
        return cli.CandidatePage(candidates=[candidate], raw_results=None, next_offset=0, has_more=False)

    monkeypatch.setattr(cli, "_tv_candidates", _fake_tv_candidates)
    monkeypatch.setattr(cli.tvmaze, "fetch_episodes", lambda *_args, **_kwargs: [])

    incoming = tmp_path / "incoming"
    library = tmp_path / "library"
    incoming.mkdir()
    library.mkdir()
    path = incoming / "Show.S01E02.mkv"
    path.write_text("x", encoding="utf-8")

    item = InferredItem(path=path, media_type="tv", title="Show", year=None, season=1, episode=2, episode_title=None)
    cache = CacheSpy(library / ".plexify" / "cache.json")

    plan, _collision = cli._process_item(
        item=item,
        library=library,
        cache=cache,
        mode="dry-run",
        copy_mode=True,
        interactive=False,
        auto_accept=True,
        min_confidence=0.55,
        session_tv=requests.Session(),
        session_wd=requests.Session(),
        episode_cache=EpisodeCache(),
        progress=None,
        show_cache=False,
        incoming_root=incoming,
        planned={},
        on_conflict="rename",
    )

    assert plan is not None
    assert cache.save_calls == 1


def test_process_item_movie_saves_cache_once_per_item(monkeypatch, tmp_path: Path) -> None:
    class CacheSpy(Cache):
        def __init__(self, path: Path) -> None:
            super().__init__(path)
            self.save_calls = 0

        def save(self) -> None:
            self.save_calls += 1
            super().save()

    def _fake_movie_candidates(*_args, **_kwargs) -> cli.CandidatePage:
        candidate = cli.Candidate(
            title="Movie",
            year=2001,
            source="Wikidata",
            confidence=1.0,
            metadata={"qid": "Q1", "title": "Movie", "year": 2001},
            enrichment=None,
        )
        return cli.CandidatePage(candidates=[candidate], raw_results=None, next_offset=0, has_more=False)

    monkeypatch.setattr(cli, "_movie_candidates", _fake_movie_candidates)

    incoming = tmp_path / "incoming"
    library = tmp_path / "library"
    incoming.mkdir()
    library.mkdir()
    path = incoming / "Movie.mkv"
    path.write_text("x", encoding="utf-8")

    item = InferredItem(path=path, media_type="movie", title="Movie", year=2001, episode_title=None)
    cache = CacheSpy(library / ".plexify" / "cache.json")

    plan, _collision = cli._process_item(
        item=item,
        library=library,
        cache=cache,
        mode="dry-run",
        copy_mode=True,
        interactive=False,
        auto_accept=True,
        min_confidence=0.55,
        session_tv=requests.Session(),
        session_wd=requests.Session(),
        episode_cache=EpisodeCache(),
        progress=None,
        show_cache=False,
        incoming_root=incoming,
        planned={},
        on_conflict="rename",
    )

    assert plan is not None
    assert cache.save_calls == 1


def test_tv_folder_cache_hit_without_year_uses_inferred_season_episode(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli.tvmaze, "search_shows", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("no api")))

    incoming = tmp_path / "incoming"
    show_folder = incoming / "Show" / "Season 1"
    show_folder.mkdir(parents=True)
    path = show_folder / "Show.S01E02.mkv"
    path.write_text("x", encoding="utf-8")

    item = InferredItem(path=path, media_type="tv", title="Show", year=None, season=1, episode=2, episode_title=None)
    cache = Cache(tmp_path / "cache.json")
    folder_key = tv_show_folder_cache_key(path, incoming)
    assert folder_key is not None
    cache.set_show(
        folder_key,
        {
            "id": 123,
            "name": "Some Show",
            "premiered": 2000,
            "chosen_year": 2000,
            "season": 9,
            "episode": 99,
            "episode_title": "Wrong",
            "manual": False,
            "confirmed_by_user": True,
        },
    )

    page = cli._tv_candidates(
        item,
        session=requests.Session(),
        cache=cache,
        show_cache=False,
        incoming_root=incoming,
        cache_key=cli.build_cache_key(path, incoming, "tv", None),
    )

    assert page.cache_hit is True
    assert page.candidates
    candidate = page.candidates[0]
    assert candidate.metadata["name"] == "Some Show"
    assert candidate.metadata.get("season") is None
    assert candidate.metadata.get("episode") is None


def test_tv_folder_cache_hit_supports_manual_entries(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli.tvmaze, "search_shows", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("no api")))

    incoming = tmp_path / "incoming"
    show_folder = incoming / "Manual Show" / "Season 1"
    show_folder.mkdir(parents=True)
    path = show_folder / "Manual.Show.S01E02.mkv"
    path.write_text("x", encoding="utf-8")

    item = InferredItem(path=path, media_type="tv", title="Manual Show", year=None, season=1, episode=2, episode_title=None)
    cache = Cache(tmp_path / "cache.json")
    folder_key = tv_show_folder_cache_key(path, incoming)
    assert folder_key is not None
    cache.set_show(
        folder_key,
        {
            "id": None,
            "name": "Manual Show",
            "premiered": None,
            "chosen_year": None,
            "manual": True,
            "confirmed_by_user": True,
        },
    )

    page = cli._tv_candidates(
        item,
        session=requests.Session(),
        cache=cache,
        show_cache=False,
        incoming_root=incoming,
        cache_key=cli.build_cache_key(path, incoming, "tv", None),
    )

    assert page.cache_hit is True
    assert page.candidates
    candidate = page.candidates[0]
    assert candidate.source == "Manual"
    assert candidate.metadata["manual"] is True
    assert candidate.metadata["name"] == "Manual Show"


def test_tv_folder_cache_written_on_confirmed_selection(monkeypatch, tmp_path: Path) -> None:
    def _fake_tv_candidates(*_args, **_kwargs) -> cli.CandidatePage:
        candidate = cli.Candidate(
            title="Show",
            year=2010,
            source="TVMaze",
            confidence=1.0,
            metadata={"id": 123, "name": "Show", "year": 2010},
            enrichment=None,
        )
        return cli.CandidatePage(candidates=[candidate], raw_results=None, next_offset=0, has_more=False)

    monkeypatch.setattr(cli, "_tv_candidates", _fake_tv_candidates)
    monkeypatch.setattr(cli, "_select_candidate", lambda *_args, **_kwargs: _args[1][0])
    monkeypatch.setattr(cli, "_maybe_enrich_candidates", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli.tvmaze, "fetch_episodes", lambda *_args, **_kwargs: [])

    incoming = tmp_path / "incoming"
    library = tmp_path / "library"
    incoming.mkdir()
    library.mkdir()
    path = incoming / "Show" / "Season 1" / "Show.S01E02.mkv"
    path.parent.mkdir(parents=True)
    path.write_text("x", encoding="utf-8")

    item = InferredItem(path=path, media_type="tv", title="Show", year=None, season=1, episode=2, episode_title=None)
    cache = Cache(library / ".plexify" / "cache.json")

    plan, _collision = cli._process_item(
        item=item,
        library=library,
        cache=cache,
        mode="dry-run",
        copy_mode=True,
        interactive=True,
        auto_accept=False,
        min_confidence=0.55,
        session_tv=requests.Session(),
        session_wd=requests.Session(),
        episode_cache=EpisodeCache(),
        progress=None,
        show_cache=False,
        incoming_root=incoming,
        planned={},
        on_conflict="rename",
    )

    assert plan is not None
    folder_key = tv_show_folder_cache_key(path, incoming)
    assert folder_key is not None
    cached = cache.get_show(folder_key)
    assert cached is not None
    assert cached.get("name") == "Show"


def test_backtracking_restores_tv_folder_cache_snapshot(monkeypatch, tmp_path: Path) -> None:
    incoming = tmp_path / "incoming"
    library = tmp_path / "library"
    incoming.mkdir()
    library.mkdir()
    first = incoming / "Show" / "Season 1" / "01.mkv"
    second = incoming / "Show" / "Season 1" / "02.mkv"
    first.parent.mkdir(parents=True)
    first.write_text("x", encoding="utf-8")
    second.write_text("x", encoding="utf-8")

    folder_key = tv_show_folder_cache_key(first, incoming)
    assert folder_key is not None
    calls = {"count": 0}

    def _process(*_args, **kwargs):
        calls["count"] += 1
        cache = kwargs["cache"]
        if calls["count"] == 1:
            cache.set_show(folder_key, {"name": "Show", "confirmed_by_user": True})
            cache.save()
            return None, False
        if calls["count"] == 2:
            raise cli.BackRequested
        return None, False

    monkeypatch.setattr(cli, "_process_item", _process)

    cli._plan_items(
        incoming=incoming,
        library=library,
        mode="dry-run",
        copy_mode=True,
        interactive=False,
        auto_accept=False,
        min_confidence=0.55,
        extensions=cli.DEFAULT_EXTENSIONS,
        cache_path=library / ".plexify" / "cache.json",
        limit=None,
        show_cache=False,
        media_type_filter=None,
        use_cache=True,
        on_conflict="rename",
    )

    final_cache = Cache(library / ".plexify" / "cache.json")
    assert final_cache.get_show(folder_key) is None


def test_auto_accept_skips_prompt_in_interactive(monkeypatch, tmp_path: Path) -> None:
    def _fake_movie_candidates(*_args, **_kwargs) -> cli.CandidatePage:
        candidate = cli.Candidate(
            title="Movie",
            year=2001,
            source="Wikidata",
            confidence=1.0,
            metadata={"qid": "Q1", "title": "Movie", "year": 2001},
            enrichment=None,
        )
        return cli.CandidatePage(candidates=[candidate], raw_results=None, next_offset=0, has_more=False)

    def _fail_prompt(*_args, **_kwargs):
        raise AssertionError("Prompt should not be called for auto-accept.")

    monkeypatch.setattr(cli, "_movie_candidates", _fake_movie_candidates)
    monkeypatch.setattr(cli.Prompt, "ask", _fail_prompt)

    incoming = tmp_path / "incoming"
    library = tmp_path / "library"
    incoming.mkdir()
    library.mkdir()
    path = incoming / "Movie.mkv"
    path.write_text("x", encoding="utf-8")

    item = InferredItem(path=path, media_type="movie", title="Movie", year=2001, episode_title=None)
    cache = Cache(library / ".plexify" / "cache.json")

    plan, _collision = cli._process_item(
        item=item,
        library=library,
        cache=cache,
        mode="dry-run",
        copy_mode=True,
        interactive=True,
        auto_accept=True,
        min_confidence=0.55,
        session_tv=requests.Session(),
        session_wd=requests.Session(),
        episode_cache=EpisodeCache(),
        progress=None,
        show_cache=False,
        incoming_root=incoming,
        planned={},
        on_conflict="rename",
    )

    assert plan is not None
