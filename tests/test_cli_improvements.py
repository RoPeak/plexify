from pathlib import Path

import requests
import typer

from plexify import cli
from plexify.cache import Cache
from plexify.infer import InferredItem
from plexify.sources import tvmaze
from plexify.util import MovePlan, movie_cache_key, tv_show_cache_key


def test_preview_selection_samples_multiple_shows(tmp_path: Path) -> None:
    plans = [
        MovePlan(
            source=tmp_path / f"A_{idx}.mkv",
            destination=tmp_path / "TV Shows" / "Show A" / f"A_{idx}.mkv",
            mode="apply",
            media_type="tv",
            metadata={"show": "Show A", "season": 1, "episode": idx},
        )
        for idx in range(1, 5)
    ]
    plans.append(
        MovePlan(
            source=tmp_path / "B_1.mkv",
            destination=tmp_path / "TV Shows" / "Show B" / "B_1.mkv",
            mode="apply",
            media_type="tv",
            metadata={"show": "Show B", "season": 1, "episode": 1},
        )
    )
    plans.append(
        MovePlan(
            source=tmp_path / "C_1.mkv",
            destination=tmp_path / "TV Shows" / "Show C" / "C_1.mkv",
            mode="apply",
            media_type="tv",
            metadata={"show": "Show C", "season": 1, "episode": 1},
        )
    )

    preview = cli._select_preview_plans(plans, limit=5)
    groups = {cli._preview_group_key(plan) for plan in preview}

    assert len(preview) == 5
    assert len(groups) >= 3


def test_movie_candidates_allow_auto_selected_reusable_cache(tmp_path: Path) -> None:
    cache = Cache(tmp_path / "cache.json")
    key = movie_cache_key("Superman II", 1980)
    cache.set_movie(
        key,
        {
            "qid": "Q1",
            "title": "Superman II",
            "year": 1980,
            "manual": False,
            "confirmed_by_user": False,
            "selection_mode": "auto",
        },
    )
    item = InferredItem(
        path=tmp_path / "Superman II.mkv",
        media_type="movie",
        title="Superman II",
        year=1980,
    )

    page = cli._movie_candidates(item, session=requests.Session(), cache=cache, show_cache=False)

    assert page.cache_hit is True
    assert page.candidates[0].title == "Superman II"


def test_tv_candidates_allow_auto_selected_reusable_cache(tmp_path: Path, monkeypatch) -> None:
    cache = Cache(tmp_path / "cache.json")
    key = tv_show_cache_key("Still Game", 2002)
    cache.set_show(
        key,
        {
            "id": 1,
            "name": "Still Game",
            "premiered": 2002,
            "manual": False,
            "confirmed_by_user": False,
            "selection_mode": "auto",
        },
    )
    item = InferredItem(
        path=tmp_path / "Still.Game.S01E01.mkv",
        media_type="tv",
        title="Still Game",
        year=2002,
        season=1,
        episode=1,
    )

    def _should_not_search(*_args, **_kwargs):
        raise AssertionError("search_shows should not be called when reusable cache is valid")

    monkeypatch.setattr(tvmaze, "search_shows", _should_not_search)
    page = cli._tv_candidates(item, session=requests.Session(), cache=cache, show_cache=False)

    assert page.cache_hit is True
    assert page.candidates[0].title == "Still Game"


def test_tv_candidates_reuse_in_run_search_cache(monkeypatch, tmp_path: Path) -> None:
    calls = {"count": 0}

    def _fake_search(*_args, **_kwargs):
        calls["count"] += 1
        return [tvmaze.TVMazeShow(id=10, name="Example Show", premiered="2020-01-01")]

    monkeypatch.setattr(tvmaze, "search_shows", _fake_search)
    cache = Cache(tmp_path / "cache.json")
    item = InferredItem(
        path=tmp_path / "Example.Show.S01E01.mkv",
        media_type="tv",
        title="Example Show",
        year=2020,
        season=1,
        episode=1,
    )
    search_cache: dict[str, list[tvmaze.TVMazeShow]] = {}

    page_one = cli._tv_candidates(
        item,
        session=requests.Session(),
        cache=cache,
        show_cache=False,
        search_cache=search_cache,
    )
    page_two = cli._tv_candidates(
        item,
        session=requests.Session(),
        cache=cache,
        show_cache=False,
        search_cache=search_cache,
    )

    assert calls["count"] == 1
    assert page_one.candidates
    assert page_two.candidates


def test_process_item_auto_resolves_implausible_episode_number(monkeypatch, tmp_path: Path) -> None:
    def _fake_tv_candidates(*_args, **_kwargs) -> cli.CandidatePage:
        candidate = cli.Candidate(
            title="Friends",
            year=1994,
            source="TVMaze",
            confidence=1.0,
            metadata={"id": 99, "name": "Friends", "year": 1994},
            enrichment=None,
        )
        return cli.CandidatePage(candidates=[candidate], raw_results=None, next_offset=0, has_more=False)

    def _fake_fetch_episodes(*_args, **_kwargs):
        return [
            tvmaze.TVMazeEpisode(season=10, number=17, name="The One With Rachel's Going Away Party"),
            tvmaze.TVMazeEpisode(season=10, number=16, name="The One With Rachel's Sister Babysits"),
        ]

    monkeypatch.setattr(cli, "_tv_candidates", _fake_tv_candidates)
    monkeypatch.setattr(cli.tvmaze, "fetch_episodes", _fake_fetch_episodes)

    incoming = tmp_path / "incoming"
    library = tmp_path / "library"
    incoming.mkdir()
    library.mkdir()
    path = incoming / "Friends" / "Friends Season 10" / "234. The One With Rachel's Going Away Party.m4v"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    item = InferredItem(
        path=path,
        media_type="tv",
        title="Friends",
        year=None,
        season=10,
        episode=234,
        episode_title="The One With Rachel's Going Away Party",
    )
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
        episode_cache=cli.EpisodeCache(),
        progress=None,
        show_cache=False,
        incoming_root=incoming,
        planned={},
        on_conflict="rename",
    )

    assert plan is not None
    assert plan.metadata["season"] == 10
    assert plan.metadata["episode"] == 17


def test_organise_dry_run_defaults_to_copy_mode(monkeypatch, tmp_path: Path) -> None:
    incoming = tmp_path / "incoming"
    library = tmp_path / "library"
    incoming.mkdir()
    library.mkdir()
    captured: dict[str, bool] = {}

    def _fake_plan_items(*_args, **kwargs):
        captured["copy_mode"] = kwargs["copy_mode"]
        return [], [], cli.PlanStats()

    monkeypatch.setattr(cli, "_initialise_logging", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_plan_items", _fake_plan_items)
    monkeypatch.setattr(cli, "write_report", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_print_run_summary", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "execute_plans", lambda *_args, **_kwargs: cli.ExecutionResult([], [], []))

    try:
        cli.organise(
            incoming=incoming,
            library=library,
            mode="dry-run",
            move=False,
            copy=False,
            extensions=cli.DEFAULT_EXTENSIONS,
            min_confidence=cli.DEFAULT_MIN_CONFIDENCE,
            cache=None,
            report=None,
            yes=False,
            limit=None,
            print_tree=False,
            interactive=False,
            no_interactive=True,
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
    except typer.Exit:
        pass

    assert captured["copy_mode"] is True


def test_organise_dry_run_respects_move_flag(monkeypatch, tmp_path: Path) -> None:
    incoming = tmp_path / "incoming"
    library = tmp_path / "library"
    incoming.mkdir()
    library.mkdir()
    captured: dict[str, bool] = {}

    def _fake_plan_items(*_args, **kwargs):
        captured["copy_mode"] = kwargs["copy_mode"]
        return [], [], cli.PlanStats()

    monkeypatch.setattr(cli, "_initialise_logging", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_plan_items", _fake_plan_items)
    monkeypatch.setattr(cli, "write_report", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_print_run_summary", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "execute_plans", lambda *_args, **_kwargs: cli.ExecutionResult([], [], []))

    try:
        cli.organise(
            incoming=incoming,
            library=library,
            mode="dry-run",
            move=True,
            copy=False,
            extensions=cli.DEFAULT_EXTENSIONS,
            min_confidence=cli.DEFAULT_MIN_CONFIDENCE,
            cache=None,
            report=None,
            yes=False,
            limit=None,
            print_tree=False,
            interactive=False,
            no_interactive=True,
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
    except typer.Exit:
        pass

    assert captured["copy_mode"] is False
