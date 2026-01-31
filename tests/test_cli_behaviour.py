from __future__ import annotations

from pathlib import Path

import requests

from plexify import cli
from plexify.cache import Cache
from plexify.util import movie_cache_key, tv_show_cache_key
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
    assert sum(progress.advanced) == 2
    descriptions = [entry.get("description", "") for entry in progress.updated]
    assert any("One.mkv" in desc for desc in descriptions)
    assert any("Two.mp4" in desc for desc in descriptions)


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


def test_tv_show_cache_does_not_override_episode(monkeypatch, tmp_path: Path) -> None:
    library = tmp_path / "library"
    library.mkdir(parents=True)
    incoming = tmp_path / "incoming"
    incoming.mkdir(parents=True)
    first_path = incoming / "Pride and Prejudice" / "1.mkv"
    second_path = incoming / "Pride and Prejudice" / "2.mkv"
    first_path.parent.mkdir(parents=True)
    first_path.write_text("one", encoding="utf-8")
    second_path.write_text("two", encoding="utf-8")

    cache = Cache(tmp_path / "cache.json")
    show_key = tv_show_cache_key("Pride and Prejudice", 1995)
    cache.set_show(
        show_key,
        {
            "id": 123,
            "name": "Pride and Prejudice",
            "premiered": 1995,
            "chosen_title": "Pride and Prejudice",
            "chosen_year": 1995,
            "manual": False,
            "confirmed_by_user": True,
            "created_at": "now",
            "source": "TVMaze",
        },
    )
    cache.save()

    planned: dict[str, int] = {}
    item1 = InferredItem(
        path=first_path,
        media_type="tv",
        title="Pride and Prejudice",
        year=1995,
        season=1,
        episode=1,
        episode_title=None,
    )
    item2 = InferredItem(
        path=second_path,
        media_type="tv",
        title="Pride and Prejudice",
        year=1995,
        season=1,
        episode=2,
        episode_title=None,
    )

    monkeypatch.setattr(cli.tvmaze, "fetch_episodes", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(cli, "_maybe_enrich_candidates", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_select_candidate", lambda *_args, **_kwargs: _args[1][0])

    plan1, _ = cli._process_item(
        item=item1,
        library=library,
        cache=cache,
        mode="dry-run",
        copy_mode=True,
        interactive=True,
        auto_accept=False,
        min_confidence=0.0,
        session_tv=requests.Session(),
        session_wd=requests.Session(),
        progress=None,
        show_cache=False,
        stats=None,
        incoming_root=incoming,
        planned=planned,
        on_conflict="rename",
        allow_back=False,
    )
    plan2, _ = cli._process_item(
        item=item2,
        library=library,
        cache=cache,
        mode="dry-run",
        copy_mode=True,
        interactive=True,
        auto_accept=False,
        min_confidence=0.0,
        session_tv=requests.Session(),
        session_wd=requests.Session(),
        progress=None,
        show_cache=False,
        stats=None,
        incoming_root=incoming,
        planned=planned,
        on_conflict="rename",
        allow_back=False,
    )
    assert plan1 is not None and plan2 is not None
    assert "s01e01" in plan1.destination.name.lower()
    assert "s01e02" in plan2.destination.name.lower()
    cached_show = cache.get_show(show_key)
    assert cached_show is not None
    assert "season" not in cached_show
    assert "episode" not in cached_show
