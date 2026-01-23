from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


@dataclass(frozen=True)
class TVMazeShow:
    id: int
    name: str
    premiered: str | None


@dataclass(frozen=True)
class TVMazeEpisode:
    season: int
    number: int
    name: str


def _session() -> requests.Session:
    session = requests.Session()
    retries = Retry(total=3, backoff_factor=0.3, status_forcelist=[429, 500, 502, 503, 504])
    session.mount("https://", HTTPAdapter(max_retries=retries))
    return session


def create_session() -> requests.Session:
    return _session()


def _rate_limit(delay: float = 0.25) -> None:
    time.sleep(delay)


def parse_show_results(payload: list[dict[str, Any]]) -> list[TVMazeShow]:
    results: list[TVMazeShow] = []
    for item in payload:
        show = item.get("show") or {}
        if not show:
            continue
        results.append(
            TVMazeShow(
                id=int(show.get("id")),
                name=str(show.get("name")),
                premiered=show.get("premiered"),
            )
        )
    return results


def parse_episode_results(payload: list[dict[str, Any]]) -> list[TVMazeEpisode]:
    results: list[TVMazeEpisode] = []
    for item in payload:
        if not item:
            continue
        results.append(
            TVMazeEpisode(
                season=int(item.get("season")),
                number=int(item.get("number")),
                name=str(item.get("name")),
            )
        )
    return results


def search_shows(query: str, session: requests.Session | None = None) -> list[TVMazeShow]:
    session = session or _session()
    resp = session.get("https://api.tvmaze.com/search/shows", params={"q": query}, timeout=10)
    resp.raise_for_status()
    _rate_limit()
    return parse_show_results(resp.json())


def fetch_episodes(show_id: int, session: requests.Session | None = None) -> list[TVMazeEpisode]:
    session = session or _session()
    resp = session.get(f"https://api.tvmaze.com/shows/{show_id}/episodes", timeout=10)
    resp.raise_for_status()
    _rate_limit()
    return parse_episode_results(resp.json())
