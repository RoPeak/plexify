from __future__ import annotations

from pathlib import Path

import requests

from plexify import cli
from plexify.cache import Cache
from plexify.tv_episode_cache import EpisodeCache
from plexify.util import movie_cache_key, tv_show_folder_cache_key
from plexify.infer import InferredItem
from plexify.sources import musicbrainz, tvmaze


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


def test_manual_movie_selection_does_not_promote_reusable_cache(monkeypatch, tmp_path: Path) -> None:
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

    incoming = tmp_path / "incoming"
    library = tmp_path / "library"
    incoming.mkdir()
    library.mkdir()
    path = incoming / "Movie.mkv"
    path.write_text("x", encoding="utf-8")

    item = InferredItem(path=path, media_type="movie", title="Movie", year=2001, episode_title=None)
    cache = Cache(library / ".plexify" / "cache.json")
    manual = cli.Candidate(
        title="Movie",
        year=2001,
        source="Manual",
        confidence=1.0,
        metadata={"qid": None, "title": "Movie", "year": 2001, "manual": True},
        enrichment=None,
    )

    monkeypatch.setattr(cli, "_movie_candidates", _fake_movie_candidates)
    monkeypatch.setattr(cli, "_select_candidate", lambda *_args, **_kwargs: "m")
    monkeypatch.setattr(cli, "_prompt_manual_movie", lambda *_args, **_kwargs: (manual, ""))
    monkeypatch.setattr(cli, "_maybe_enrich_candidates", lambda *_args, **_kwargs: None)

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
    assert cache.get_movie(movie_cache_key(item.title, item.year)) is None


def test_auto_movie_selection_does_not_promote_reusable_cache_when_gap_is_tight(monkeypatch, tmp_path: Path) -> None:
    def _fake_movie_candidates(*_args, **_kwargs) -> cli.CandidatePage:
        primary = cli.Candidate(
            title="Movie",
            year=2001,
            source="Wikidata",
            confidence=0.97,
            metadata={"qid": "Q1", "title": "Movie", "year": 2001},
            enrichment=None,
        )
        secondary = cli.Candidate(
            title="Movie (Alt)",
            year=2002,
            source="Wikidata",
            confidence=0.93,
            metadata={"qid": "Q2", "title": "Movie (Alt)", "year": 2002},
            enrichment=None,
        )
        return cli.CandidatePage(candidates=[primary, secondary], raw_results=None, next_offset=0, has_more=False)

    incoming = tmp_path / "incoming"
    library = tmp_path / "library"
    incoming.mkdir()
    library.mkdir()
    path = incoming / "Movie.mkv"
    path.write_text("x", encoding="utf-8")

    item = InferredItem(path=path, media_type="movie", title="Movie", year=2001, episode_title=None)
    cache = Cache(library / ".plexify" / "cache.json")

    monkeypatch.setattr(cli, "_movie_candidates", _fake_movie_candidates)

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
    cache_key = cli.build_cache_key(path, incoming, "movie", item.year)
    assert cache.get_movie(cache_key) is not None
    assert cache.get_movie(movie_cache_key(item.title, item.year)) is None


def test_conflicting_reusable_movie_matches_mark_query_key_ambiguous(monkeypatch, tmp_path: Path) -> None:
    def _fake_movie_candidates(item: InferredItem, *_args, **_kwargs) -> cli.CandidatePage:
        qid = "Q1" if item.path.parent.name == "A" else "Q2"
        candidate = cli.Candidate(
            title="The Office",
            year=2005,
            source="Wikidata",
            confidence=0.99,
            metadata={"qid": qid, "title": "The Office", "year": 2005},
            enrichment=None,
        )
        return cli.CandidatePage(candidates=[candidate], raw_results=None, next_offset=0, has_more=False)

    incoming = tmp_path / "incoming"
    library = tmp_path / "library"
    incoming.mkdir()
    library.mkdir()
    path_a = incoming / "A" / "The.Office.S01E01.mkv"
    path_b = incoming / "B" / "The.Office.S01E01.mkv"
    path_a.parent.mkdir(parents=True)
    path_b.parent.mkdir(parents=True)
    path_a.write_text("x", encoding="utf-8")
    path_b.write_text("x", encoding="utf-8")

    cache = Cache(library / ".plexify" / "cache.json")
    monkeypatch.setattr(cli, "_movie_candidates", _fake_movie_candidates)

    for path in [path_a, path_b]:
        item = InferredItem(path=path, media_type="movie", title="The Office", year=2005, episode_title=None)
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

    reusable_key = movie_cache_key("The Office", 2005)
    reusable = cache.get_movie(reusable_key)
    assert reusable is not None
    assert reusable.get("ambiguous") is True
    assert len(reusable.get("matches", [])) == 2

    monkeypatch.setattr(cli.wikidata, "search", lambda *_args, **_kwargs: [])
    fresh_item = InferredItem(
        path=incoming / "C" / "The.Office.S01E01.mkv",
        media_type="movie",
        title="The Office",
        year=2005,
        episode_title=None,
    )
    page = cli._movie_candidates(
        fresh_item,
        session=requests.Session(),
        cache=cache,
        show_cache=False,
        cache_key="movie|path|c/the.office.s01e01.mkv|the office s01e01|2005",
    )
    assert page.cache_hit is False


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


def test_tv_ambiguous_reusable_key_falls_back_to_folder_cache(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli.tvmaze, "search_shows", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("no api")))

    incoming = tmp_path / "incoming"
    path = incoming / "Show" / "Season 1" / "Show.S01E02.mkv"
    path.parent.mkdir(parents=True)
    path.write_text("x", encoding="utf-8")
    item = InferredItem(path=path, media_type="tv", title="Show", year=2005, season=1, episode=2, episode_title=None)
    cache = Cache(tmp_path / "cache.json")

    reusable_show_key = cli.tv_show_cache_key(item.title, item.year)
    folder_key = tv_show_folder_cache_key(path, incoming)
    assert folder_key is not None
    cache.set_show(
        reusable_show_key,
        {
            "ambiguous": True,
            "matches": [{"id": "10", "title": "Show (US)", "year": 2005}, {"id": "20", "title": "Show (UK)", "year": 2001}],
        },
    )
    cache.set_show(folder_key, _tv_cache_entry(id_value=2, name="Folder Show", premiered=2005))

    page = cli._tv_candidates(
        item,
        session=requests.Session(),
        cache=cache,
        show_cache=False,
        incoming_root=incoming,
        cache_key=cli.build_cache_key(path, incoming, "tv", item.year),
    )

    assert page.cache_hit is True
    assert page.candidates[0].metadata["name"] == "Folder Show"


def test_tv_reusable_show_cache_does_not_override_inferred_episode_in_plan(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli.tvmaze, "search_shows", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("no api")))
    monkeypatch.setattr(cli.tvmaze, "fetch_episodes", lambda *_args, **_kwargs: [])

    incoming = tmp_path / "incoming"
    library = tmp_path / "library"
    path = incoming / "Show" / "Season 1" / "Show.S01E02.mkv"
    path.parent.mkdir(parents=True)
    library.mkdir()
    path.write_text("x", encoding="utf-8")

    item = InferredItem(path=path, media_type="tv", title="Show", year=2005, season=1, episode=2, episode_title=None)
    cache = Cache(tmp_path / "cache.json")
    cache.set_show(
        cli.tv_show_cache_key(item.title, item.year),
        {
            "id": 123,
            "name": "Show",
            "premiered": 2005,
            "season": 9,
            "episode": 99,
            "episode_title": "Wrong",
            "manual": False,
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
    assert plan.metadata["season"] == 1
    assert plan.metadata["episode"] == 2


def test_tv_episode_cache_not_reused_for_ambiguous_title(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli.tvmaze, "search_shows", lambda *_args, **_kwargs: [])

    incoming = tmp_path / "incoming"
    path = incoming / "Supergirl Season 1" / "11. Strange Visitor From Another Planet.m4v"
    path.parent.mkdir(parents=True)
    path.write_text("x", encoding="utf-8")

    # Simulates prior bad inference creating an ambiguous title in cache keys.
    item = InferredItem(path=path, media_type="tv", title="Incoming", year=None, season=1, episode=11, episode_title=None)
    cache = Cache(tmp_path / "cache.json")

    episode_key = cli.tv_episode_cache_key(item.title, item.year, item.season, item.episode)
    cache.set_show(
        episode_key,
        {
            "id": 4,
            "name": "DC's Legends of Tomorrow",
            "premiered": 2016,
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
        cache_key=cli.build_cache_key(path, incoming, "tv", item.year),
    )

    assert page.cache_hit is False


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


def test_organise_apply_overwrite_requires_extra_token(monkeypatch, tmp_path: Path) -> None:
    incoming = tmp_path / "incoming"
    library = tmp_path / "library"
    incoming.mkdir()
    library.mkdir()
    src = incoming / "Movie.mkv"
    src.write_text("x", encoding="utf-8")
    plan = cli.MovePlan(
        source=src,
        destination=library / "Movies" / "Movie (2000)" / "Movie (2000).mkv",
        mode="apply",
        media_type="movie",
        metadata={"title": "Movie", "year": 2000},
    )
    called = {"apply": 0}

    monkeypatch.setattr(cli, "_initialise_logging", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_plan_items", lambda *_args, **_kwargs: ([plan], [], cli.PlanStats()))
    monkeypatch.setattr(cli, "write_report", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_print_run_summary", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_confirm", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(cli, "_confirm_overwrite_apply", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        cli,
        "_apply_with_progress",
        lambda *_args, **_kwargs: called.__setitem__("apply", called["apply"] + 1) or cli.ExecutionResult([], [], []),
    )

    try:
        cli.organise(
            incoming=incoming,
            library=library,
            mode="apply",
            move=False,
            copy=True,
            extensions=cli.DEFAULT_EXTENSIONS,
            min_confidence=cli.DEFAULT_MIN_CONFIDENCE,
            cache=None,
            report=None,
            yes=False,
            limit=None,
            print_tree=False,
            interactive=True,
            no_interactive=False,
            media_type="auto",
            no_cache=False,
            clear_cache=False,
            offline=False,
            on_conflict="overwrite",
            log_level="WARNING",
            log_format="text",
            log_file=None,
            prune_empty_dirs=False,
        )
    except cli.typer.Exit as exc:
        assert exc.exit_code == 0

    assert called["apply"] == 0


def test_organise_apply_overwrite_token_allows_apply(monkeypatch, tmp_path: Path) -> None:
    incoming = tmp_path / "incoming"
    library = tmp_path / "library"
    incoming.mkdir()
    library.mkdir()
    src = incoming / "Movie.mkv"
    src.write_text("x", encoding="utf-8")
    plan = cli.MovePlan(
        source=src,
        destination=library / "Movies" / "Movie (2000)" / "Movie (2000).mkv",
        mode="apply",
        media_type="movie",
        metadata={"title": "Movie", "year": 2000},
    )
    called = {"apply": 0}

    monkeypatch.setattr(cli, "_initialise_logging", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_plan_items", lambda *_args, **_kwargs: ([plan], [], cli.PlanStats()))
    monkeypatch.setattr(cli, "write_report", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_print_run_summary", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_confirm", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(cli, "_confirm_overwrite_apply", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        cli,
        "_apply_with_progress",
        lambda *_args, **_kwargs: called.__setitem__("apply", called["apply"] + 1)
        or cli.ExecutionResult([plan], [], []),
    )

    try:
        cli.organise(
            incoming=incoming,
            library=library,
            mode="apply",
            move=False,
            copy=True,
            extensions=cli.DEFAULT_EXTENSIONS,
            min_confidence=cli.DEFAULT_MIN_CONFIDENCE,
            cache=None,
            report=None,
            yes=False,
            limit=None,
            print_tree=False,
            interactive=True,
            no_interactive=False,
            media_type="auto",
            no_cache=False,
            clear_cache=False,
            offline=False,
            on_conflict="overwrite",
            log_level="WARNING",
            log_format="text",
            log_file=None,
            prune_empty_dirs=False,
        )
    except cli.typer.Exit as exc:
        assert exc.exit_code == 0

    assert called["apply"] == 1


def test_organise_apply_rename_does_not_prompt_overwrite_token(monkeypatch, tmp_path: Path) -> None:
    incoming = tmp_path / "incoming"
    library = tmp_path / "library"
    incoming.mkdir()
    library.mkdir()
    src = incoming / "Movie.mkv"
    src.write_text("x", encoding="utf-8")
    plan = cli.MovePlan(
        source=src,
        destination=library / "Movies" / "Movie (2000)" / "Movie (2000).mkv",
        mode="apply",
        media_type="movie",
        metadata={"title": "Movie", "year": 2000},
    )
    called = {"apply": 0}

    monkeypatch.setattr(cli, "_initialise_logging", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_plan_items", lambda *_args, **_kwargs: ([plan], [], cli.PlanStats()))
    monkeypatch.setattr(cli, "write_report", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_print_run_summary", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_confirm", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        cli,
        "_confirm_overwrite_apply",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not be called")),
    )
    monkeypatch.setattr(
        cli,
        "_apply_with_progress",
        lambda *_args, **_kwargs: called.__setitem__("apply", called["apply"] + 1)
        or cli.ExecutionResult([plan], [], []),
    )

    try:
        cli.organise(
            incoming=incoming,
            library=library,
            mode="apply",
            move=False,
            copy=True,
            extensions=cli.DEFAULT_EXTENSIONS,
            min_confidence=cli.DEFAULT_MIN_CONFIDENCE,
            cache=None,
            report=None,
            yes=False,
            limit=None,
            print_tree=False,
            interactive=True,
            no_interactive=False,
            media_type="auto",
            no_cache=False,
            clear_cache=False,
            offline=False,
            on_conflict="rename",
            log_level="WARNING",
            log_format="text",
            log_file=None,
            prune_empty_dirs=False,
        )
    except cli.typer.Exit as exc:
        assert exc.exit_code == 0

    assert called["apply"] == 1


def test_music_mismatch_reprompts_and_filename_fallback_restores_album(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "incoming"
    library = tmp_path / "library"
    album = source / "Eminem - Curtain Call"
    album.mkdir(parents=True)
    library.mkdir()
    (album / "01 - Eminem - Intro.flac").write_text("x", encoding="utf-8")

    candidate = musicbrainz.ReleaseCandidate(
        mbid="mb1",
        title="Curtain Call 2",
        artist="Eminem",
        year=2022,
        country="US",
        score=0.95,
        track_count=2,
    )
    mb_tracks = [
        musicbrainz.Track(number=1, title="Intro", disc=1),
        musicbrainz.Track(number=2, title="Track Two", disc=1),
    ]

    prompt_answers = iter(["2", "f"])
    prompt_calls: list[str] = []
    captured: dict[str, list[cli.MovePlan]] = {}

    monkeypatch.setattr(cli, "_initialise_logging", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_save_wizard_prefs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "write_report", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli.musicbrainz, "is_available", lambda: True)
    monkeypatch.setattr(cli.musicbrainz, "unavailable_reason", lambda: None)
    monkeypatch.setattr(cli.musicbrainz, "create_session", lambda: requests.Session())
    monkeypatch.setattr(cli.musicbrainz, "search_releases", lambda *_args, **_kwargs: [candidate])
    monkeypatch.setattr(cli.musicbrainz, "fetch_release_tracks", lambda *_args, **_kwargs: mb_tracks)
    monkeypatch.setattr(cli, "_select_music_candidate", lambda *_args, **_kwargs: candidate)

    def _fake_prompt_choice(prompt: str, *_args, **_kwargs) -> str:
        prompt_calls.append(prompt)
        return next(prompt_answers)

    monkeypatch.setattr(cli, "_prompt_choice", _fake_prompt_choice)

    def _fake_execute(plans, **_kwargs):
        captured["plans"] = list(plans)
        return cli.ExecutionResult(moved=[], skipped=[], errors=[])

    monkeypatch.setattr(cli, "execute_plans", _fake_execute)

    try:
        cli.music(
            source=source,
            library=library,
            apply=False,
            copy=True,
            extensions=cli.DEFAULT_MUSIC_EXTENSIONS,
            verify=True,
            keep_art=False,
            keep_cue=False,
            keep_log=False,
            offline=False,
            cleanup_empty_dirs=False,
            verbose_plan=False,
            log_level="WARNING",
            log_format="text",
            log_file=None,
        )
    except cli.typer.Exit as exc:
        assert exc.exit_code == 0

    planned = captured["plans"]
    assert planned
    assert any("Music\\Eminem\\Curtain Call\\" in str(plan.destination) for plan in planned)
    assert not any("Curtain Call 2" in str(plan.destination) for plan in planned)
    assert len(prompt_calls) == 2


def test_music_uses_single_session_and_caches_release_tracklists(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "incoming"
    library = tmp_path / "library"
    album_one = source / "Artist - Album One"
    album_two = source / "Artist - Album Two"
    album_one.mkdir(parents=True)
    album_two.mkdir(parents=True)
    library.mkdir()
    (album_one / "01 - Artist - Track One.flac").write_text("x", encoding="utf-8")
    (album_two / "01 - Artist - Track Two.flac").write_text("x", encoding="utf-8")

    class _Session:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    session = _Session()
    candidate = musicbrainz.ReleaseCandidate(
        mbid="shared-mbid",
        title="Album",
        artist="Artist",
        year=2000,
        country="US",
        score=0.95,
        track_count=1,
    )
    search_sessions: list[object] = []
    fetch_sessions: list[object] = []

    monkeypatch.setattr(cli, "_initialise_logging", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_save_wizard_prefs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "write_report", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli.musicbrainz, "is_available", lambda: True)
    monkeypatch.setattr(cli.musicbrainz, "unavailable_reason", lambda: None)
    monkeypatch.setattr(cli.musicbrainz, "create_session", lambda: session)
    monkeypatch.setattr(
        cli.musicbrainz,
        "search_releases",
        lambda *_args, **kwargs: search_sessions.append(kwargs.get("session")) or [candidate],
    )
    monkeypatch.setattr(
        cli.musicbrainz,
        "fetch_release_tracks",
        lambda *_args, **kwargs: fetch_sessions.append(kwargs.get("session"))
        or [musicbrainz.Track(number=1, title="Track", disc=1)],
    )
    monkeypatch.setattr(cli, "_select_music_candidate", lambda *_args, **_kwargs: candidate)
    monkeypatch.setattr(
        cli,
        "execute_plans",
        lambda plans, **_kwargs: cli.ExecutionResult(moved=[], skipped=list(plans), errors=[]),
    )

    try:
        cli.music(
            source=source,
            library=library,
            apply=False,
            copy=True,
            extensions=cli.DEFAULT_MUSIC_EXTENSIONS,
            verify=True,
            keep_art=False,
            keep_cue=False,
            keep_log=False,
            offline=False,
            cleanup_empty_dirs=False,
            verbose_plan=False,
            log_level="WARNING",
            log_format="text",
            log_file=None,
        )
    except cli.typer.Exit as exc:
        assert exc.exit_code == 0

    assert len(search_sessions) == 2
    assert len(fetch_sessions) == 1
    assert all(call is session for call in search_sessions)
    assert all(call is session for call in fetch_sessions)
    assert session.closed is True


def test_music_auto_maps_multidisc_without_disc_numbers(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "incoming"
    library = tmp_path / "library"
    album = source / "Artist - Album"
    album.mkdir(parents=True)
    library.mkdir()
    (album / "01 - Artist - Intro.flac").write_text("x", encoding="utf-8")
    (album / "02 - Artist - Finale.flac").write_text("x", encoding="utf-8")

    candidate = musicbrainz.ReleaseCandidate(
        mbid="mb-multi",
        title="Album Deluxe",
        artist="Artist",
        year=2000,
        country="US",
        score=0.95,
        track_count=2,
    )
    mb_tracks = [
        musicbrainz.Track(number=1, title="Intro (MB)", disc=1),
        musicbrainz.Track(number=1, title="Finale (MB)", disc=2),
    ]
    captured: dict[str, list[cli.MovePlan]] = {}

    monkeypatch.setattr(cli, "_initialise_logging", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_save_wizard_prefs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "write_report", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli.musicbrainz, "is_available", lambda: True)
    monkeypatch.setattr(cli.musicbrainz, "unavailable_reason", lambda: None)
    monkeypatch.setattr(cli.musicbrainz, "create_session", lambda: requests.Session())
    monkeypatch.setattr(cli.musicbrainz, "search_releases", lambda *_args, **_kwargs: [candidate])
    monkeypatch.setattr(cli.musicbrainz, "fetch_release_tracks", lambda *_args, **_kwargs: mb_tracks)
    monkeypatch.setattr(cli, "_select_music_candidate", lambda *_args, **_kwargs: candidate)
    monkeypatch.setattr(
        cli,
        "_confirm",
        lambda prompt, *_args, **_kwargs: (_ for _ in ()).throw(AssertionError(f"Unexpected confirm: {prompt}")),
    )

    def _fake_execute(plans, **_kwargs):
        captured["plans"] = list(plans)
        return cli.ExecutionResult(moved=[], skipped=[], errors=[])

    monkeypatch.setattr(cli, "execute_plans", _fake_execute)

    try:
        cli.music(
            source=source,
            library=library,
            apply=False,
            copy=True,
            extensions=cli.DEFAULT_MUSIC_EXTENSIONS,
            verify=True,
            keep_art=False,
            keep_cue=False,
            keep_log=False,
            offline=False,
            cleanup_empty_dirs=False,
            verbose_plan=False,
            plan_preview_tracks=0,
            log_level="WARNING",
            log_format="text",
            log_file=None,
        )
    except cli.typer.Exit as exc:
        assert exc.exit_code == 0

    planned = captured["plans"]
    assert planned
    destinations = [str(plan.destination) for plan in planned]
    assert any("Music\\Artist\\Album Deluxe\\101 - Intro (MB).flac" in destination for destination in destinations)
    assert any("Music\\Artist\\Album Deluxe\\201 - Finale (MB).flac" in destination for destination in destinations)


def test_music_auto_accepts_top_candidate_and_passes_inferred_year(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "incoming"
    library = tmp_path / "library"
    album = source / "Duffy - Rockferry (2008)"
    album.mkdir(parents=True)
    library.mkdir()
    (album / "01 - Duffy - Rockferry.flac").write_text("x", encoding="utf-8")

    candidates = [
        musicbrainz.ReleaseCandidate(
            mbid="top",
            title="Rockferry",
            artist="Duffy",
            year=2008,
            country="GB",
            score=1.0,
            track_count=1,
            raw_score=1.0,
        ),
        musicbrainz.ReleaseCandidate(
            mbid="other",
            title="Rockferry Deluxe",
            artist="Duffy",
            year=2008,
            country="US",
            score=0.60,
            track_count=3,
            raw_score=0.60,
        ),
    ]
    search_years: list[int | None] = []
    fetch_calls: list[str] = []
    captured: dict[str, list[cli.MovePlan]] = {}

    monkeypatch.setattr(cli, "_initialise_logging", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_save_wizard_prefs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "write_report", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli.musicbrainz, "is_available", lambda: True)
    monkeypatch.setattr(cli.musicbrainz, "unavailable_reason", lambda: None)
    monkeypatch.setattr(cli.musicbrainz, "create_session", lambda: requests.Session())
    monkeypatch.setattr(
        cli.musicbrainz,
        "search_releases",
        lambda *_args, **kwargs: search_years.append(kwargs.get("year")) or candidates,
    )
    monkeypatch.setattr(
        cli.musicbrainz,
        "fetch_release_tracks",
        lambda mbid, **_kwargs: fetch_calls.append(mbid) or [musicbrainz.Track(number=1, title="Track", disc=1)],
    )
    monkeypatch.setattr(
        cli,
        "_select_music_candidate",
        lambda *_args, **_kwargs: candidate,
    )

    def _fake_execute(plans, **_kwargs):
        captured["plans"] = list(plans)
        return cli.ExecutionResult(moved=[], skipped=[], errors=[])

    monkeypatch.setattr(cli, "execute_plans", _fake_execute)

    try:
        cli.music(
            source=source,
            library=library,
            apply=False,
            copy=True,
            extensions=cli.DEFAULT_MUSIC_EXTENSIONS,
            verify=True,
            keep_art=False,
            keep_cue=False,
            keep_log=False,
            offline=False,
            cleanup_empty_dirs=False,
            verbose_plan=False,
            plan_preview_tracks=0,
            log_level="WARNING",
            log_format="text",
            log_file=None,
        )
    except cli.typer.Exit as exc:
        assert exc.exit_code == 0

    assert search_years == [2008]
    assert fetch_calls == ["top"]
    assert captured["plans"]


def test_music_auto_skips_extreme_track_mismatch_without_selection_prompt(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "incoming"
    library = tmp_path / "library"
    album = source / "Artist - Album"
    album.mkdir(parents=True)
    library.mkdir()
    (album / "01 - Artist - Song.flac").write_text("x", encoding="utf-8")

    candidate = musicbrainz.ReleaseCandidate(
        mbid="mismatch",
        title="Album Anthology",
        artist="Artist",
        year=2001,
        country="US",
        score=1.0,
        track_count=40,
        raw_score=1.0,
    )
    fetch_called = {"value": False}
    captured: dict[str, list[cli.MovePlan]] = {}

    monkeypatch.setattr(cli, "_initialise_logging", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_save_wizard_prefs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "write_report", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli.musicbrainz, "is_available", lambda: True)
    monkeypatch.setattr(cli.musicbrainz, "unavailable_reason", lambda: None)
    monkeypatch.setattr(cli.musicbrainz, "create_session", lambda: requests.Session())
    monkeypatch.setattr(cli.musicbrainz, "search_releases", lambda *_args, **_kwargs: [candidate])
    monkeypatch.setattr(
        cli.musicbrainz,
        "fetch_release_tracks",
        lambda *_args, **_kwargs: fetch_called.update(value=True) or [],
    )
    monkeypatch.setattr(
        cli,
        "_select_music_candidate",
        lambda *_args, **_kwargs: candidate,
    )

    def _fake_execute(plans, **_kwargs):
        captured["plans"] = list(plans)
        return cli.ExecutionResult(moved=[], skipped=[], errors=[])

    monkeypatch.setattr(cli, "execute_plans", _fake_execute)

    try:
        cli.music(
            source=source,
            library=library,
            apply=False,
            copy=True,
            extensions=cli.DEFAULT_MUSIC_EXTENSIONS,
            verify=True,
            keep_art=False,
            keep_cue=False,
            keep_log=False,
            offline=False,
            cleanup_empty_dirs=False,
            verbose_plan=False,
            plan_preview_tracks=0,
            log_level="WARNING",
            log_format="text",
            log_file=None,
        )
    except cli.typer.Exit as exc:
        assert exc.exit_code == 0

    assert fetch_called["value"] is False
    assert captured["plans"]
    assert all("Music\\Artist\\Album\\" in str(plan.destination) for plan in captured["plans"])


def test_music_auto_uses_later_exact_match_when_top_is_extreme_mismatch(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "incoming"
    library = tmp_path / "library"
    album = source / "Artist - Album (2001)"
    album.mkdir(parents=True)
    library.mkdir()
    (album / "01 - Artist - Song.flac").write_text("x", encoding="utf-8")

    candidates = [
        musicbrainz.ReleaseCandidate(
            mbid="top-mismatch",
            title="Album Anthology",
            artist="Artist",
            year=2001,
            country="US",
            score=1.0,
            track_count=40,
            raw_score=1.0,
        ),
        musicbrainz.ReleaseCandidate(
            mbid="exact-match",
            title="Album",
            artist="Artist",
            year=2001,
            country="GB",
            score=0.996,
            track_count=1,
            raw_score=1.0,
        ),
    ]
    fetch_calls: list[str] = []
    captured: dict[str, list[cli.MovePlan]] = {}

    monkeypatch.setattr(cli, "_initialise_logging", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_save_wizard_prefs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "write_report", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli.musicbrainz, "is_available", lambda: True)
    monkeypatch.setattr(cli.musicbrainz, "unavailable_reason", lambda: None)
    monkeypatch.setattr(cli.musicbrainz, "create_session", lambda: requests.Session())
    monkeypatch.setattr(cli.musicbrainz, "search_releases", lambda *_args, **_kwargs: candidates)
    monkeypatch.setattr(
        cli.musicbrainz,
        "fetch_release_tracks",
        lambda mbid, **_kwargs: fetch_calls.append(mbid) or [musicbrainz.Track(number=1, title="Song (MB)", disc=1)],
    )
    monkeypatch.setattr(
        cli,
        "_select_music_candidate",
        lambda *_args, **_kwargs: candidate,
    )

    def _fake_execute(plans, **_kwargs):
        captured["plans"] = list(plans)
        return cli.ExecutionResult(moved=[], skipped=[], errors=[])

    monkeypatch.setattr(cli, "execute_plans", _fake_execute)

    try:
        cli.music(
            source=source,
            library=library,
            apply=False,
            copy=True,
            extensions=cli.DEFAULT_MUSIC_EXTENSIONS,
            verify=True,
            keep_art=False,
            keep_cue=False,
            keep_log=False,
            offline=False,
            cleanup_empty_dirs=False,
            verbose_plan=False,
            plan_preview_tracks=0,
            log_level="WARNING",
            log_format="text",
            log_file=None,
        )
    except cli.typer.Exit as exc:
        assert exc.exit_code == 0

    assert fetch_calls == ["exact-match"]
    assert captured["plans"]
    assert any("Music\\Artist\\Album\\01 - Song (MB).flac" in str(plan.destination) for plan in captured["plans"])


def test_music_skip_all_remaining_verification_applies_to_later_albums(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "incoming"
    library = tmp_path / "library"
    album_one = source / "Artist - One"
    album_two = source / "Artist - Two"
    album_one.mkdir(parents=True)
    album_two.mkdir(parents=True)
    library.mkdir()
    (album_one / "01 - Artist - Track One.flac").write_text("x", encoding="utf-8")
    (album_two / "01 - Artist - Track Two.flac").write_text("x", encoding="utf-8")

    candidate = musicbrainz.ReleaseCandidate(
        mbid="pick",
        title="One",
        artist="Artist",
        year=2000,
        country="US",
        score=0.99,
        track_count=1,
        raw_score=0.99,
    )
    search_calls = {"count": 0}
    captured: dict[str, list[cli.MovePlan]] = {}

    monkeypatch.setattr(cli, "_initialise_logging", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_save_wizard_prefs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "write_report", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli.musicbrainz, "is_available", lambda: True)
    monkeypatch.setattr(cli.musicbrainz, "unavailable_reason", lambda: None)
    monkeypatch.setattr(cli.musicbrainz, "create_session", lambda: requests.Session())
    monkeypatch.setattr(
        cli.musicbrainz,
        "search_releases",
        lambda *_args, **_kwargs: search_calls.update(count=search_calls["count"] + 1) or [candidate],
    )
    monkeypatch.setattr(
        cli.musicbrainz,
        "fetch_release_tracks",
        lambda *_args, **_kwargs: [musicbrainz.Track(number=1, title="Track", disc=1)],
    )
    monkeypatch.setattr(
        cli,
        "_select_music_candidate",
        lambda *_args, **_kwargs: "skip_all",
    )

    def _fake_execute(plans, **_kwargs):
        captured["plans"] = list(plans)
        return cli.ExecutionResult(moved=[], skipped=[], errors=[])

    monkeypatch.setattr(cli, "execute_plans", _fake_execute)

    try:
        cli.music(
            source=source,
            library=library,
            apply=False,
            copy=True,
            extensions=cli.DEFAULT_MUSIC_EXTENSIONS,
            verify=True,
            keep_art=False,
            keep_cue=False,
            keep_log=False,
            offline=False,
            cleanup_empty_dirs=False,
            verbose_plan=False,
            plan_preview_tracks=0,
            mismatch_policy="ask",
            log_level="WARNING",
            log_format="text",
            log_file=None,
        )
    except cli.typer.Exit as exc:
        assert exc.exit_code == 0

    assert search_calls["count"] == 1
    assert captured["plans"]


def test_music_does_not_save_wizard_prefs_when_paths_provided(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "incoming"
    library = tmp_path / "library"
    album_dir = source / "Artist - Album"
    album_dir.mkdir(parents=True)
    library.mkdir()
    (album_dir / "01 - Artist - Track.flac").write_text("x", encoding="utf-8")

    saved_calls = {"count": 0}
    monkeypatch.setattr(cli, "_initialise_logging", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_save_wizard_prefs", lambda *_args, **_kwargs: saved_calls.update(count=saved_calls["count"] + 1))
    monkeypatch.setattr(cli, "write_report", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        cli,
        "execute_plans",
        lambda plans, **_kwargs: cli.ExecutionResult(moved=[], skipped=list(plans), errors=[]),
    )

    try:
        cli.music(
            source=source,
            library=library,
            apply=False,
            copy=True,
            extensions=cli.DEFAULT_MUSIC_EXTENSIONS,
            verify=False,
            keep_art=False,
            keep_cue=False,
            keep_log=False,
            offline=False,
            cleanup_empty_dirs=False,
            verbose_plan=False,
            plan_preview_tracks=0,
            mismatch_policy="ask",
            log_level="WARNING",
            log_format="text",
            log_file=None,
        )
    except cli.typer.Exit as exc:
        assert exc.exit_code == 0

    assert saved_calls["count"] == 0


def test_prompt_music_track_mismatch_choice_honours_policy() -> None:
    assert cli._prompt_music_track_mismatch_choice(mismatch_policy="filename") == "f"
    assert cli._prompt_music_track_mismatch_choice(mismatch_policy="order") == "o"


def test_music_generic_metadata_uses_dominant_track_artist_override(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "incoming"
    library = tmp_path / "library"
    album_dir = source / "Various Artists" / "Ladies & Gentlemen - The Best of George Michael"
    album_dir.mkdir(parents=True)
    library.mkdir()
    (album_dir / "01 - George Michael - Jesus to a Child.flac").write_text("x", encoding="utf-8")
    (album_dir / "02 - George Michael - Father Figure.flac").write_text("x", encoding="utf-8")
    (album_dir / "03 - George Michael - One More Try.flac").write_text("x", encoding="utf-8")
    (album_dir / "04 - George Michael - Freedom! '90.flac").write_text("x", encoding="utf-8")
    (album_dir / "05 - Guest Singer - Bonus Track.flac").write_text("x", encoding="utf-8")

    search_artists: list[str] = []
    candidate = musicbrainz.ReleaseCandidate(
        mbid="gm-best-of",
        title="Ladies & Gentlemen - The Best of George Michael",
        artist="George Michael",
        year=1998,
        country="GB",
        score=1.0,
        track_count=5,
        raw_score=1.0,
    )

    monkeypatch.setattr(cli, "_initialise_logging", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_save_wizard_prefs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "write_report", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli.musicbrainz, "is_available", lambda: True)
    monkeypatch.setattr(cli.musicbrainz, "unavailable_reason", lambda: None)
    monkeypatch.setattr(cli.musicbrainz, "create_session", lambda: requests.Session())
    monkeypatch.setattr(
        cli.musicbrainz,
        "search_releases",
        lambda artist, *_args, **_kwargs: search_artists.append(artist) or [candidate],
    )
    monkeypatch.setattr(
        cli.musicbrainz,
        "fetch_release_tracks",
        lambda *_args, **_kwargs: [musicbrainz.Track(number=index, title=f"Track {index} (MB)", disc=1) for index in range(1, 6)],
    )
    monkeypatch.setattr(cli, "_select_music_candidate", lambda *_args, **_kwargs: candidate)
    monkeypatch.setattr(
        cli,
        "execute_plans",
        lambda plans, **_kwargs: cli.ExecutionResult(moved=[], skipped=list(plans), errors=[]),
    )

    try:
        cli.music(
            source=source,
            library=library,
            apply=False,
            copy=True,
            extensions=cli.DEFAULT_MUSIC_EXTENSIONS,
            verify=True,
            keep_art=False,
            keep_cue=False,
            keep_log=False,
            offline=False,
            cleanup_empty_dirs=False,
            cleanup_unknown_files=False,
            verbose_plan=False,
            plan_preview_tracks=0,
            mismatch_policy="ask",
            log_level="WARNING",
            log_format="text",
            log_file=None,
        )
    except cli.typer.Exit as exc:
        assert exc.exit_code == 0

    assert search_artists == ["George Michael"]


def test_music_auto_prompts_when_top_candidate_gap_is_ambiguous(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "incoming"
    library = tmp_path / "library"
    album = source / "Artist - Album (2001)"
    album.mkdir(parents=True)
    library.mkdir()
    (album / "01 - Artist - Song.flac").write_text("x", encoding="utf-8")

    top = musicbrainz.ReleaseCandidate(
        mbid="top",
        title="Album",
        artist="Artist",
        year=2001,
        country="GB",
        score=1.0,
        track_count=1,
        raw_score=1.0,
    )
    second = musicbrainz.ReleaseCandidate(
        mbid="second",
        title="Album",
        artist="Artist",
        year=2001,
        country="US",
        score=0.995,
        track_count=1,
        raw_score=0.995,
    )
    selection_calls = {"count": 0}

    monkeypatch.setattr(cli, "_initialise_logging", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_save_wizard_prefs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "write_report", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli.musicbrainz, "is_available", lambda: True)
    monkeypatch.setattr(cli.musicbrainz, "unavailable_reason", lambda: None)
    monkeypatch.setattr(cli.musicbrainz, "create_session", lambda: requests.Session())
    monkeypatch.setattr(cli.musicbrainz, "search_releases", lambda *_args, **_kwargs: [top, second])
    monkeypatch.setattr(cli, "_rank_music_candidates", lambda *_args, **_kwargs: [top, second])
    monkeypatch.setattr(
        cli.musicbrainz,
        "fetch_release_tracks",
        lambda *_args, **_kwargs: [musicbrainz.Track(number=1, title="Song (MB)", disc=1)],
    )
    monkeypatch.setattr(
        cli,
        "_select_music_candidate",
        lambda *_args, **_kwargs: selection_calls.update(count=selection_calls["count"] + 1) or top,
    )
    monkeypatch.setattr(
        cli,
        "execute_plans",
        lambda plans, **_kwargs: cli.ExecutionResult(moved=[], skipped=list(plans), errors=[]),
    )

    try:
        cli.music(
            source=source,
            library=library,
            apply=False,
            copy=True,
            extensions=cli.DEFAULT_MUSIC_EXTENSIONS,
            verify=True,
            keep_art=False,
            keep_cue=False,
            keep_log=False,
            offline=False,
            cleanup_empty_dirs=False,
            cleanup_unknown_files=False,
            verbose_plan=False,
            plan_preview_tracks=0,
            mismatch_policy="ask",
            log_level="WARNING",
            log_format="text",
            log_file=None,
        )
    except cli.typer.Exit as exc:
        assert exc.exit_code == 0

    assert selection_calls["count"] == 1


def test_music_cleanup_unknown_without_token_keeps_leftovers_in_interactive_mode(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "incoming"
    library = tmp_path / "library"
    album_dir = source / "Artist - Album"
    album_dir.mkdir(parents=True)
    library.mkdir()
    track = album_dir / "01 - Artist - Track.flac"
    leftover = album_dir / "notes.txt"
    track.write_text("x", encoding="utf-8")
    leftover.write_text("keep", encoding="utf-8")

    class _Stdin:
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr(cli.sys, "stdin", _Stdin())
    monkeypatch.setattr(cli, "_initialise_logging", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_save_wizard_prefs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "write_report", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_prompt_text", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(cli, "_confirm_move", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        cli,
        "_apply_with_progress",
        lambda plans, **_kwargs: cli.ExecutionResult(moved=list(plans), skipped=[], errors=[]),
    )

    try:
        cli.music(
            source=source,
            library=library,
            apply=True,
            copy=False,
            extensions=cli.DEFAULT_MUSIC_EXTENSIONS,
            verify=False,
            keep_art=False,
            keep_cue=False,
            keep_log=False,
            offline=False,
            cleanup_empty_dirs=True,
            cleanup_unknown_files=True,
            verbose_plan=False,
            plan_preview_tracks=0,
            mismatch_policy="ask",
            log_level="WARNING",
            log_format="text",
            log_file=None,
        )
    except cli.typer.Exit as exc:
        assert exc.exit_code == 0

    assert leftover.exists()


def test_music_cleanup_unknown_noninteractive_requires_confirm_token(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "incoming"
    library = tmp_path / "library"
    album_dir = source / "Artist - Album"
    album_dir.mkdir(parents=True)
    library.mkdir()
    (album_dir / "01 - Artist - Track.flac").write_text("x", encoding="utf-8")
    (album_dir / "notes.txt").write_text("remove", encoding="utf-8")

    class _Stdin:
        def isatty(self) -> bool:
            return False

    monkeypatch.setattr(cli.sys, "stdin", _Stdin())
    monkeypatch.setattr(cli, "_initialise_logging", lambda *_args, **_kwargs: None)

    try:
        cli.music(
            source=source,
            library=library,
            apply=True,
            copy=False,
            extensions=cli.DEFAULT_MUSIC_EXTENSIONS,
            verify=False,
            keep_art=False,
            keep_cue=False,
            keep_log=False,
            offline=False,
            cleanup_empty_dirs=True,
            cleanup_unknown_files=True,
            verbose_plan=False,
            plan_preview_tracks=0,
            mismatch_policy="ask",
            log_level="WARNING",
            log_format="text",
            log_file=None,
        )
    except cli.typer.Exit as exc:
        assert exc.exit_code == 2


def test_music_cleanup_unknown_noninteractive_with_valid_token_deletes_leftovers(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "incoming"
    library = tmp_path / "library"
    album_dir = source / "Artist - Album"
    album_dir.mkdir(parents=True)
    library.mkdir()
    (album_dir / "01 - Artist - Track.flac").write_text("x", encoding="utf-8")
    leftover = album_dir / "notes.txt"
    leftover.write_text("remove", encoding="utf-8")

    class _Stdin:
        def isatty(self) -> bool:
            return False

    monkeypatch.setattr(cli.sys, "stdin", _Stdin())
    monkeypatch.setattr(cli, "_initialise_logging", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_save_wizard_prefs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "write_report", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_confirm_move", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        cli,
        "_apply_with_progress",
        lambda plans, **_kwargs: cli.ExecutionResult(moved=list(plans), skipped=[], errors=[]),
    )

    try:
        cli.music(
            source=source,
            library=library,
            apply=True,
            copy=False,
            extensions=cli.DEFAULT_MUSIC_EXTENSIONS,
            verify=False,
            keep_art=False,
            keep_cue=False,
            keep_log=False,
            offline=False,
            cleanup_empty_dirs=True,
            cleanup_unknown_files=True,
            cleanup_unknown_confirm_token="REMOVE-UNKNOWN",
            verbose_plan=False,
            plan_preview_tracks=0,
            mismatch_policy="ask",
            log_level="WARNING",
            log_format="text",
            log_file=None,
        )
    except cli.typer.Exit as exc:
        assert exc.exit_code == 0

    assert not leftover.exists()


def test_music_verifies_various_artists_without_track_artist_signal(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "incoming"
    library = tmp_path / "library"
    album_dir = source / "Various Artists" / "Sampler"
    album_dir.mkdir(parents=True)
    library.mkdir()
    (album_dir / "01 - Song One.flac").write_text("x", encoding="utf-8")
    (album_dir / "02 - Song Two.flac").write_text("x", encoding="utf-8")
    search_calls: list[tuple[str, str]] = []
    candidate = musicbrainz.ReleaseCandidate(
        mbid="sampler",
        title="Sampler",
        artist="Various Artists",
        year=2001,
        country="GB",
        score=1.0,
        track_count=2,
        raw_score=1.0,
    )

    monkeypatch.setattr(cli, "_initialise_logging", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_save_wizard_prefs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "write_report", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli.musicbrainz, "is_available", lambda: True)
    monkeypatch.setattr(cli.musicbrainz, "unavailable_reason", lambda: None)
    monkeypatch.setattr(cli.musicbrainz, "create_session", lambda: requests.Session())
    monkeypatch.setattr(
        cli.musicbrainz,
        "search_releases",
        lambda artist, album, *_args, **_kwargs: search_calls.append((artist, album)) or [candidate],
    )
    monkeypatch.setattr(
        cli.musicbrainz,
        "fetch_release_tracks",
        lambda *_args, **_kwargs: [
            musicbrainz.Track(number=1, title="Song One (MB)", disc=1),
            musicbrainz.Track(number=2, title="Song Two (MB)", disc=1),
        ],
    )
    monkeypatch.setattr(
        cli,
        "_select_music_candidate",
        lambda *_args, **_kwargs: candidate,
    )
    monkeypatch.setattr(
        cli,
        "execute_plans",
        lambda plans, **_kwargs: cli.ExecutionResult(moved=[], skipped=list(plans), errors=[]),
    )

    try:
        cli.music(
            source=source,
            library=library,
            apply=False,
            copy=True,
            extensions=cli.DEFAULT_MUSIC_EXTENSIONS,
            verify=True,
            keep_art=False,
            keep_cue=False,
            keep_log=False,
            offline=False,
            cleanup_empty_dirs=False,
            cleanup_unknown_files=False,
            verbose_plan=False,
            plan_preview_tracks=0,
            mismatch_policy="ask",
            log_level="WARNING",
            log_format="text",
            log_file=None,
        )
    except cli.typer.Exit as exc:
        assert exc.exit_code == 0

    assert search_calls == [("Various Artists", "Sampler")]


def test_music_no_matches_can_retry_with_edited_query(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "incoming"
    library = tmp_path / "library"
    album_dir = source / "Take That" / "Never Forget - The Ultimate Collection"
    album_dir.mkdir(parents=True)
    library.mkdir()
    (album_dir / "01 - Never Forget.flac").write_text("x", encoding="utf-8")

    class _Stdin:
        def isatty(self) -> bool:
            return True

    search_calls: list[tuple[str, str]] = []
    candidate = musicbrainz.ReleaseCandidate(
        mbid="never-forget",
        title="Never Forget: The Ultimate Collection",
        artist="Take That",
        year=1999,
        country="GB",
        score=1.0,
        track_count=1,
        raw_score=1.0,
    )
    prompt_values = iter(["Take That", "Never Forget: The Ultimate Collection"])
    captured: dict[str, list[cli.MovePlan]] = {}

    monkeypatch.setattr(cli.sys, "stdin", _Stdin())
    monkeypatch.setattr(cli, "_initialise_logging", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_save_wizard_prefs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "write_report", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli.musicbrainz, "is_available", lambda: True)
    monkeypatch.setattr(cli.musicbrainz, "unavailable_reason", lambda: None)
    monkeypatch.setattr(cli.musicbrainz, "create_session", lambda: requests.Session())
    monkeypatch.setattr(
        cli.musicbrainz,
        "search_releases",
        lambda artist, album, *_args, **_kwargs: (
            search_calls.append((artist, album)),
            [] if len(search_calls) == 1 else [candidate],
        )[1],
    )
    monkeypatch.setattr(
        cli.musicbrainz,
        "fetch_release_tracks",
        lambda *_args, **_kwargs: [musicbrainz.Track(number=1, title="Never Forget (MB)", disc=1)],
    )
    monkeypatch.setattr(cli, "_prompt_choice_loop", lambda *_args, **_kwargs: "r")
    monkeypatch.setattr(cli, "_prompt_text", lambda *_args, **_kwargs: next(prompt_values))
    monkeypatch.setattr(cli, "_select_music_candidate", lambda *_args, **_kwargs: candidate)
    def _execute(plans, **_kwargs):
        captured["plans"] = list(plans)
        return cli.ExecutionResult(moved=[], skipped=list(plans), errors=[])

    monkeypatch.setattr(cli, "execute_plans", _execute)

    try:
        cli.music(
            source=source,
            library=library,
            apply=False,
            copy=True,
            extensions=cli.DEFAULT_MUSIC_EXTENSIONS,
            verify=True,
            keep_art=False,
            keep_cue=False,
            keep_log=False,
            offline=False,
            cleanup_empty_dirs=False,
            cleanup_unknown_files=False,
            verbose_plan=False,
            plan_preview_tracks=0,
            mismatch_policy="ask",
            log_level="WARNING",
            log_format="text",
            log_file=None,
        )
    except cli.typer.Exit as exc:
        assert exc.exit_code == 0

    assert search_calls == [
        ("Take That", "Never Forget - The Ultimate Collection"),
        ("Take That", "Never Forget: The Ultimate Collection"),
    ]
    assert captured["plans"][0].destination.name == "01 - Never Forget (MB).flac"


def test_music_no_matches_can_fallback_to_filename_metadata(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "incoming"
    library = tmp_path / "library"
    album_dir = source / "Artist" / "Album"
    album_dir.mkdir(parents=True)
    library.mkdir()
    (album_dir / "01 - Song One.flac").write_text("x", encoding="utf-8")

    class _Stdin:
        def isatty(self) -> bool:
            return True

    captured: dict[str, list[cli.MovePlan]] = {}

    monkeypatch.setattr(cli.sys, "stdin", _Stdin())
    monkeypatch.setattr(cli, "_initialise_logging", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_save_wizard_prefs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "write_report", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli.musicbrainz, "is_available", lambda: True)
    monkeypatch.setattr(cli.musicbrainz, "unavailable_reason", lambda: None)
    monkeypatch.setattr(cli.musicbrainz, "create_session", lambda: requests.Session())
    monkeypatch.setattr(cli.musicbrainz, "search_releases", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(cli, "_prompt_choice_loop", lambda *_args, **_kwargs: "f")
    def _execute(plans, **_kwargs):
        captured["plans"] = list(plans)
        return cli.ExecutionResult(moved=[], skipped=list(plans), errors=[])

    monkeypatch.setattr(cli, "execute_plans", _execute)

    try:
        cli.music(
            source=source,
            library=library,
            apply=False,
            copy=True,
            extensions=cli.DEFAULT_MUSIC_EXTENSIONS,
            verify=True,
            keep_art=False,
            keep_cue=False,
            keep_log=False,
            offline=False,
            cleanup_empty_dirs=False,
            cleanup_unknown_files=False,
            verbose_plan=False,
            plan_preview_tracks=0,
            mismatch_policy="ask",
            log_level="WARNING",
            log_format="text",
            log_file=None,
        )
    except cli.typer.Exit as exc:
        assert exc.exit_code == 0

    assert captured["plans"][0].destination.name == "01 - Song One.flac"


def test_music_no_matches_can_skip_album_verification(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "incoming"
    library = tmp_path / "library"
    album_dir = source / "Artist" / "Album"
    album_dir.mkdir(parents=True)
    library.mkdir()
    (album_dir / "01 - Song One.flac").write_text("x", encoding="utf-8")

    class _Stdin:
        def isatty(self) -> bool:
            return True

    search_calls = {"count": 0}
    captured: dict[str, list[cli.MovePlan]] = {}

    monkeypatch.setattr(cli.sys, "stdin", _Stdin())
    monkeypatch.setattr(cli, "_initialise_logging", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_save_wizard_prefs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "write_report", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli.musicbrainz, "is_available", lambda: True)
    monkeypatch.setattr(cli.musicbrainz, "unavailable_reason", lambda: None)
    monkeypatch.setattr(cli.musicbrainz, "create_session", lambda: requests.Session())
    monkeypatch.setattr(
        cli.musicbrainz,
        "search_releases",
        lambda *_args, **_kwargs: search_calls.update(count=search_calls["count"] + 1) or [],
    )
    monkeypatch.setattr(cli, "_prompt_choice_loop", lambda *_args, **_kwargs: "s")
    def _execute(plans, **_kwargs):
        captured["plans"] = list(plans)
        return cli.ExecutionResult(moved=[], skipped=list(plans), errors=[])

    monkeypatch.setattr(cli, "execute_plans", _execute)

    try:
        cli.music(
            source=source,
            library=library,
            apply=False,
            copy=True,
            extensions=cli.DEFAULT_MUSIC_EXTENSIONS,
            verify=True,
            keep_art=False,
            keep_cue=False,
            keep_log=False,
            offline=False,
            cleanup_empty_dirs=False,
            cleanup_unknown_files=False,
            verbose_plan=False,
            plan_preview_tracks=0,
            mismatch_policy="ask",
            log_level="WARNING",
            log_format="text",
            log_file=None,
        )
    except cli.typer.Exit as exc:
        assert exc.exit_code == 0

    assert search_calls["count"] == 1
    assert captured["plans"][0].destination.name == "01 - Song One.flac"
