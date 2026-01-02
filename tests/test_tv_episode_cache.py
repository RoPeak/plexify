from __future__ import annotations

import requests

from plexify.sources import tvmaze
from plexify.tv_episode_cache import EpisodeCache


def test_episode_cache_fetches_once(monkeypatch) -> None:
    calls = {"count": 0}

    def _fake_fetch(*_args, **_kwargs):
        calls["count"] += 1
        return [tvmaze.TVMazeEpisode(season=1, number=1, name="Pilot")]

    monkeypatch.setattr(tvmaze, "fetch_episodes", _fake_fetch)

    cache = EpisodeCache()
    session = requests.Session()
    first = cache.get_episodes(42, session=session)
    second = cache.get_episodes(42, session=session)

    assert calls["count"] == 1
    assert first == second
