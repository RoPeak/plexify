from __future__ import annotations

from pathlib import Path

import requests

from plexify import cli
from plexify.cache import Cache
from plexify.tv_episode_cache import EpisodeCache
from plexify.util import movie_cache_key, tv_show_folder_cache_key
from plexify.infer import InferredItem
from plexify.sources import tvmaze


def _tv_cache_entry(
    *,
    id_value: int,
    name: str,
    premiered: int,
    season: int | None = None,
    episode: int | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": id_value,
        "name": name,
        "premiered": premiered,
        "chosen_title": name,
        "chosen_year": premiered,
        "manual": False,
        "confirmed_by_user": True,
        "source": "TVMaze",
    }
    if season is not None:
        payload["season"] = season
    if episode is not None:
        payload["episode"] = episode
    return payload


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


def test_prompt_season_allows_skip_with_k(monkeypatch, tmp_path: Path) -> None:
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
    monkeypatch.setattr(cli, "_prompt_text", lambda *_args, **_kwargs: "k")
    monkeypatch.setattr(cli.tvmaze, "fetch_episodes", lambda *_args, **_kwargs: [])

    incoming = tmp_path / "incoming"
    library = tmp_path / "library"
    incoming.mkdir()
    library.mkdir()
    path = incoming / "Show" / "Season X" / "Gag Reel.avi"
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

    assert plan is None


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


def test_tv_cache_precedence_episode_over_reusable_folder_and_file(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli.tvmaze, "search_shows", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("no api")))

    incoming = tmp_path / "incoming"
    path = incoming / "Show" / "Season 1" / "Show.S01E02.mkv"
    path.parent.mkdir(parents=True)
    path.write_text("x", encoding="utf-8")
    item = InferredItem(path=path, media_type="tv", title="Show", year=2005, season=1, episode=2, episode_title=None)
    cache = Cache(tmp_path / "cache.json")

    path_key = cli.build_cache_key(path, incoming, "tv", item.year)
    episode_key = cli.tv_episode_cache_key(item.title, item.year, item.season, item.episode)
    reusable_show_key = cli.tv_show_cache_key(item.title, item.year)
    folder_key = tv_show_folder_cache_key(path, incoming)
    assert folder_key is not None

    cache.set_show(path_key, _tv_cache_entry(id_value=1, name="File Show", premiered=2005, season=8, episode=88))
    cache.set_show(folder_key, _tv_cache_entry(id_value=2, name="Folder Show", premiered=2005))
    cache.set_show(reusable_show_key, _tv_cache_entry(id_value=3, name="Reusable Show", premiered=2005))
    cache.set_show(episode_key, _tv_cache_entry(id_value=4, name="Episode Show", premiered=2005, season=9, episode=99))

    page = cli._tv_candidates(
        item,
        session=requests.Session(),
        cache=cache,
        show_cache=False,
        incoming_root=incoming,
        cache_key=path_key,
    )

    assert page.cache_hit is True
    candidate = page.candidates[0]
    assert candidate.metadata["name"] == "Episode Show"
    assert candidate.metadata.get("season") == 9
    assert candidate.metadata.get("episode") == 99


def test_tv_cache_precedence_reusable_over_folder_and_file(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli.tvmaze, "search_shows", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("no api")))

    incoming = tmp_path / "incoming"
    path = incoming / "Show" / "Season 1" / "Show.S01E02.mkv"
    path.parent.mkdir(parents=True)
    path.write_text("x", encoding="utf-8")
    item = InferredItem(path=path, media_type="tv", title="Show", year=2005, season=1, episode=2, episode_title=None)
    cache = Cache(tmp_path / "cache.json")

    path_key = cli.build_cache_key(path, incoming, "tv", item.year)
    reusable_show_key = cli.tv_show_cache_key(item.title, item.year)
    folder_key = tv_show_folder_cache_key(path, incoming)
    assert folder_key is not None

    cache.set_show(path_key, _tv_cache_entry(id_value=1, name="File Show", premiered=2005, season=8, episode=88))
    cache.set_show(folder_key, _tv_cache_entry(id_value=2, name="Folder Show", premiered=2005))
    cache.set_show(reusable_show_key, _tv_cache_entry(id_value=3, name="Reusable Show", premiered=2005))

    page = cli._tv_candidates(
        item,
        session=requests.Session(),
        cache=cache,
        show_cache=False,
        incoming_root=incoming,
        cache_key=path_key,
    )

    assert page.cache_hit is True
    candidate = page.candidates[0]
    assert candidate.metadata["name"] == "Reusable Show"
    assert candidate.metadata.get("season") is None
    assert candidate.metadata.get("episode") is None


def test_tv_cache_precedence_folder_over_file(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli.tvmaze, "search_shows", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("no api")))

    incoming = tmp_path / "incoming"
    path = incoming / "Show" / "Season 1" / "Show.S01E02.mkv"
    path.parent.mkdir(parents=True)
    path.write_text("x", encoding="utf-8")
    item = InferredItem(path=path, media_type="tv", title="Show", year=None, season=1, episode=2, episode_title=None)
    cache = Cache(tmp_path / "cache.json")

    path_key = cli.build_cache_key(path, incoming, "tv", item.year)
    folder_key = tv_show_folder_cache_key(path, incoming)
    assert folder_key is not None

    cache.set_show(path_key, _tv_cache_entry(id_value=1, name="File Show", premiered=2005, season=8, episode=88))
    cache.set_show(folder_key, _tv_cache_entry(id_value=2, name="Folder Show", premiered=2005))

    page = cli._tv_candidates(
        item,
        session=requests.Session(),
        cache=cache,
        show_cache=False,
        incoming_root=incoming,
        cache_key=path_key,
    )

    assert page.cache_hit is True
    candidate = page.candidates[0]
    assert candidate.metadata["name"] == "Folder Show"
    assert candidate.metadata.get("season") is None
    assert candidate.metadata.get("episode") is None


def test_tv_cache_file_used_when_higher_precedence_keys_missing(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli.tvmaze, "search_shows", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("no api")))

    incoming = tmp_path / "incoming"
    path = incoming / "Show" / "Season 1" / "Show.S01E02.mkv"
    path.parent.mkdir(parents=True)
    path.write_text("x", encoding="utf-8")
    item = InferredItem(path=path, media_type="tv", title="Show", year=None, season=1, episode=2, episode_title=None)
    cache = Cache(tmp_path / "cache.json")

    path_key = cli.build_cache_key(path, incoming, "tv", item.year)
    cache.set_show(path_key, _tv_cache_entry(id_value=1, name="File Show", premiered=2005, season=8, episode=88))

    page = cli._tv_candidates(
        item,
        session=requests.Session(),
        cache=cache,
        show_cache=False,
        incoming_root=incoming,
        cache_key=path_key,
    )

    assert page.cache_hit is True
    candidate = page.candidates[0]
    assert candidate.metadata["name"] == "File Show"
    assert candidate.metadata.get("season") == 8
    assert candidate.metadata.get("episode") == 88


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


def test_tv_folder_manual_cache_hit_carries_season(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli.tvmaze, "search_shows", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("no api")))

    incoming = tmp_path / "incoming"
    show_folder = incoming / "Manual Show" / "Seaon 5"
    show_folder.mkdir(parents=True)
    path = show_folder / "10. Episode.mkv"
    path.write_text("x", encoding="utf-8")

    item = InferredItem(path=path, media_type="tv", title="Manual Show", year=None, season=1, episode=10, episode_title=None)
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
            "season": 5,
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
    candidate = page.candidates[0]
    assert candidate.metadata["manual"] is True
    assert candidate.metadata.get("season") == 5


def test_tv_search_retries_with_normalized_query(monkeypatch, tmp_path: Path) -> None:
    queries: list[str] = []

    def _fake_search(query: str, *_args, **_kwargs):
        queries.append(query)
        if len(queries) == 1:
            return []
        return [tvmaze.TVMazeShow(id=1, name="The Big Bang Theory", premiered=2007)]

    monkeypatch.setattr(cli.tvmaze, "search_shows", _fake_search)

    item = InferredItem(
        path=Path("Show/Seaon 5/1. Episode.mkv"),
        media_type="tv",
        title="The Big Bang Theory Seaon 5",
        year=None,
        season=1,
        episode=1,
        episode_title=None,
    )
    page = cli._tv_candidates(
        item,
        session=requests.Session(),
        cache=Cache(tmp_path / "cache.json"),
        show_cache=False,
        search_query="The Big Bang Theory Seaon 5 cast",
    )

    assert len(queries) == 2
    assert queries[0] == "The Big Bang Theory Seaon 5 cast"
    assert queries[1] == "the big bang theory cast"
    assert page.candidates
    assert page.candidates[0].title == "The Big Bang Theory"


def test_process_item_uses_folder_manual_season_lock(monkeypatch, tmp_path: Path) -> None:
    def _fake_tv_candidates(item: InferredItem, *_args, **_kwargs) -> cli.CandidatePage:
        candidate = cli.Candidate(
            title="Manual Show",
            year=2010,
            source="TVMaze",
            confidence=1.0,
            metadata={"id": 123, "name": "Manual Show", "year": 2010, "episode": item.episode},
            enrichment=None,
        )
        return cli.CandidatePage(candidates=[candidate], raw_results=None, next_offset=0, has_more=False)

    monkeypatch.setattr(cli, "_tv_candidates", _fake_tv_candidates)
    monkeypatch.setattr(cli.tvmaze, "fetch_episodes", lambda *_args, **_kwargs: [])

    incoming = tmp_path / "incoming"
    library = tmp_path / "library"
    show_folder = incoming / "Manual Show" / "Seaon 5"
    show_folder.mkdir(parents=True)
    library.mkdir()
    path = show_folder / "10. Episode.mkv"
    path.write_text("x", encoding="utf-8")

    item = InferredItem(path=path, media_type="tv", title="Manual Show", year=None, season=1, episode=10, episode_title=None)
    cache = Cache(library / ".plexify" / "cache.json")
    folder_key = tv_show_folder_cache_key(path, incoming)
    assert folder_key is not None
    cache.set_show(
        folder_key,
        {
            "id": None,
            "name": "Manual Show",
            "premiered": None,
            "chosen_year": None,
            "season": 5,
            "manual": True,
            "confirmed_by_user": True,
        },
    )

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
    assert plan.metadata["season"] == 5


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


def test_tv_long_path_warns_but_plans(monkeypatch, tmp_path: Path) -> None:
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

    long_destination = tmp_path / ("x" * 260 + ".mkv")
    messages: list[str] = []
    monkeypatch.setattr(cli, "_tv_candidates", _fake_tv_candidates)
    monkeypatch.setattr(cli, "plan_tv_show", lambda *_args, **_kwargs: long_destination)
    monkeypatch.setattr(cli.tvmaze, "fetch_episodes", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(cli, "_safe_print", lambda message, _progress=None: messages.append(str(message)))

    incoming = tmp_path / "incoming"
    library = tmp_path / "library"
    incoming.mkdir()
    library.mkdir()
    path = incoming / "Show.S01E02.mkv"
    path.write_text("x", encoding="utf-8")
    item = InferredItem(path=path, media_type="tv", title="Show", year=2010, season=1, episode=2, episode_title=None)
    cache = Cache(tmp_path / "cache.json")

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
    assert any("destination path is very long" in message for message in messages)


def test_movie_long_path_warns_but_plans(monkeypatch, tmp_path: Path) -> None:
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

    long_destination = tmp_path / ("y" * 260 + ".mkv")
    messages: list[str] = []
    monkeypatch.setattr(cli, "_movie_candidates", _fake_movie_candidates)
    monkeypatch.setattr(cli, "plan_movie", lambda *_args, **_kwargs: long_destination)
    monkeypatch.setattr(cli, "_safe_print", lambda message, _progress=None: messages.append(str(message)))

    incoming = tmp_path / "incoming"
    library = tmp_path / "library"
    incoming.mkdir()
    library.mkdir()
    path = incoming / "Movie.mkv"
    path.write_text("x", encoding="utf-8")
    item = InferredItem(path=path, media_type="movie", title="Movie", year=2001, episode_title=None)
    cache = Cache(tmp_path / "cache.json")

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
    assert any("destination path is very long" in message for message in messages)


def test_tv_long_path_collision_rename_stays_stable(monkeypatch, tmp_path: Path) -> None:
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

    long_destination = tmp_path / ("z" * 260 + ".mkv")
    monkeypatch.setattr(cli, "_tv_candidates", _fake_tv_candidates)
    monkeypatch.setattr(cli, "plan_tv_show", lambda *_args, **_kwargs: long_destination)
    monkeypatch.setattr(cli.tvmaze, "fetch_episodes", lambda *_args, **_kwargs: [])

    incoming = tmp_path / "incoming"
    library = tmp_path / "library"
    incoming.mkdir()
    library.mkdir()
    path = incoming / "Show.S01E02.mkv"
    path.write_text("x", encoding="utf-8")
    item = InferredItem(path=path, media_type="tv", title="Show", year=2010, season=1, episode=2, episode_title=None)
    cache = Cache(tmp_path / "cache.json")
    planned = {str(long_destination).lower(): 1}

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
        planned=planned,
        on_conflict="rename",
    )

    assert plan is not None
    assert plan.destination != long_destination
    assert "(2)" in plan.destination.stem


def test_no_movie_candidates_can_switch_to_tv_search(monkeypatch) -> None:
    def _fake_movie_candidates(*_args, **_kwargs) -> cli.CandidatePage:
        return cli.CandidatePage(candidates=[], raw_results=[], next_offset=0, has_more=False)

    def _fake_tv_candidates(*_args, **_kwargs) -> cli.CandidatePage:
        candidate = cli.Candidate(
            title="Arrested Development",
            year=2003,
            source="TVMaze",
            confidence=1.0,
            metadata={"id": 123, "name": "Arrested Development", "year": 2003},
            enrichment=None,
        )
        return cli.CandidatePage(candidates=[candidate], raw_results=None, next_offset=0, has_more=False)

    confirms = iter([True, True])
    monkeypatch.setattr(cli, "_movie_candidates", _fake_movie_candidates)
    monkeypatch.setattr(cli, "_tv_candidates", _fake_tv_candidates)
    monkeypatch.setattr(cli, "_confirm", lambda *_args, **_kwargs: next(confirms))
    monkeypatch.setattr(cli.tvmaze, "fetch_episodes", lambda *_args, **_kwargs: [])

    incoming = Path("plexify")
    library = Path("tests")
    path = incoming / "Arrested Development" / "1. Pilot.mkv"

    item = InferredItem(path=path, media_type="movie", title="1 Pilot", year=None, season=1, episode=1, episode_title="Pilot")
    cache = cli.NullCache()

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
    assert plan.media_type == "tv"
    assert "TV Shows" in str(plan.destination)


def test_no_tv_candidates_can_switch_to_movie_search(monkeypatch) -> None:
    def _fake_tv_candidates(*_args, **_kwargs) -> cli.CandidatePage:
        return cli.CandidatePage(candidates=[], raw_results=[], next_offset=0, has_more=False)

    def _fake_movie_candidates(*_args, **_kwargs) -> cli.CandidatePage:
        candidate = cli.Candidate(
            title="Example Film",
            year=2001,
            source="Wikidata",
            confidence=1.0,
            metadata={"qid": "Q1", "title": "Example Film", "year": 2001},
            enrichment=None,
        )
        return cli.CandidatePage(candidates=[candidate], raw_results=None, next_offset=0, has_more=False)

    confirms = iter([True, True])
    monkeypatch.setattr(cli, "_tv_candidates", _fake_tv_candidates)
    monkeypatch.setattr(cli, "_movie_candidates", _fake_movie_candidates)
    monkeypatch.setattr(cli, "_confirm", lambda *_args, **_kwargs: next(confirms))

    incoming = Path("plexify")
    library = Path("tests")
    path = incoming / "Show.S01E01.mkv"

    item = InferredItem(path=path, media_type="tv", title="Show", year=None, season=1, episode=1, episode_title=None)
    cache = cli.NullCache()

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
    assert plan.media_type == "movie"
    assert "Movies" in str(plan.destination)


def test_movie_to_tv_switch_persists_for_same_folder(monkeypatch, tmp_path: Path) -> None:
    movie_calls = {"count": 0}
    confirm_prompts: list[str] = []

    def _fake_movie_candidates(*_args, **_kwargs) -> cli.CandidatePage:
        movie_calls["count"] += 1
        return cli.CandidatePage(candidates=[], raw_results=[], next_offset=0, has_more=False)

    def _fake_tv_candidates(item: InferredItem, *_args, **_kwargs) -> cli.CandidatePage:
        candidate = cli.Candidate(
            title="Arrested Development",
            year=2003,
            source="TVMaze",
            confidence=1.0,
            metadata={"id": 123, "name": "Arrested Development", "year": 2003, "season": item.season, "episode": item.episode},
            enrichment=None,
        )
        return cli.CandidatePage(candidates=[candidate], raw_results=None, next_offset=0, has_more=False)

    def _fake_confirm(prompt: str, *_args, **_kwargs) -> bool:
        confirm_prompts.append(prompt)
        return "Switch to TV search?" in prompt

    monkeypatch.setattr(cli, "_movie_candidates", _fake_movie_candidates)
    monkeypatch.setattr(cli, "_tv_candidates", _fake_tv_candidates)
    monkeypatch.setattr(cli, "_confirm", _fake_confirm)
    monkeypatch.setattr(cli.tvmaze, "fetch_episodes", lambda *_args, **_kwargs: [])

    incoming = tmp_path / "incoming"
    library = tmp_path / "library"
    show_dir = incoming / "Arrested Development"
    show_dir.mkdir(parents=True)
    library.mkdir()
    first_path = show_dir / "1. Pilot.mkv"
    second_path = show_dir / "2. Top Banana.mkv"
    first_path.write_text("x", encoding="utf-8")
    second_path.write_text("x", encoding="utf-8")

    cache = Cache(tmp_path / "cache.json")
    overrides: dict[str, str] = {}
    first = InferredItem(
        path=first_path,
        media_type="movie",
        title="1 Pilot",
        year=None,
        season=1,
        episode=1,
        episode_title="Pilot",
    )
    second = InferredItem(
        path=second_path,
        media_type="movie",
        title="2 Top Banana",
        year=None,
        season=1,
        episode=2,
        episode_title="Top Banana",
    )

    plan_one, _ = cli._process_item(
        item=first,
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
        media_type_overrides=overrides,
    )
    assert plan_one is not None
    assert plan_one.media_type == "tv"

    plan_two, _ = cli._process_item(
        item=second,
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
        media_type_overrides=overrides,
    )
    assert plan_two is not None
    assert plan_two.media_type == "tv"
    assert movie_calls["count"] == 1
    assert sum("Switch to TV search?" in prompt for prompt in confirm_prompts) == 1
