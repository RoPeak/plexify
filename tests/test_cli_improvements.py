from pathlib import Path

import requests

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
