from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..logging_config import get_logger


_available = True
_warned = False
_recover_at: float | None = None
_unavailable_reason: str | None = None
logger = get_logger(__name__)


def _warn_unavailable(message: str) -> None:
    global _warned
    if _warned:
        return
    logger.warning(message)
    _warned = True


def _set_unavailable(message: str, *, cooldown: float = 60.0) -> None:
    global _available, _recover_at, _unavailable_reason
    if not _available:
        return
    _available = False
    _recover_at = time.monotonic() + cooldown
    _unavailable_reason = message
    _warn_unavailable(message)


def is_available() -> bool:
    global _available, _recover_at, _warned, _unavailable_reason
    if not _available and _recover_at is not None and time.monotonic() >= _recover_at:
        _available = True
        _recover_at = None
        _warned = False
        _unavailable_reason = None
    return _available


def unavailable_reason() -> str | None:
    return _unavailable_reason


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


@dataclass(frozen=True)
class TVMazeShowDetails:
    network: str | None
    creator: str | None
    cast: list[str]


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
        show_id = show.get("id")
        name = show.get("name")
        if not isinstance(show_id, int) or not isinstance(name, str) or not name.strip():
            continue
        results.append(
            TVMazeShow(
                id=show_id,
                name=name,
                premiered=show.get("premiered"),
            )
        )
    return results


def parse_episode_results(payload: list[dict[str, Any]]) -> list[TVMazeEpisode]:
    results: list[TVMazeEpisode] = []
    for item in payload:
        if not item:
            continue
        season = item.get("season")
        number = item.get("number")
        name = item.get("name")
        if not isinstance(season, int) or not isinstance(number, int) or not isinstance(name, str) or not name.strip():
            continue
        results.append(
            TVMazeEpisode(
                season=season,
                number=number,
                name=name,
            )
        )
    return results


def _extract_network_name(show: dict[str, Any]) -> str | None:
    network = show.get("network") or {}
    if network:
        name = network.get("name")
        if name:
            return str(name)
    web_channel = show.get("webChannel") or {}
    if web_channel:
        name = web_channel.get("name")
        if name:
            return str(name)
    return None


def _parse_cast(payload: dict[str, Any]) -> list[str]:
    cast_entries = payload.get("_embedded", {}).get("cast") or []
    cast: list[str] = []
    for entry in cast_entries:
        person = entry.get("person") or {}
        name = person.get("name")
        if name:
            cast.append(str(name))
    return cast


def _parse_creator(payload: list[dict[str, Any]]) -> str | None:
    for entry in payload:
        if entry.get("type") == "Creator":
            person = entry.get("person") or {}
            name = person.get("name")
            if name:
                return str(name)
    return None


def search_shows(
    query: str,
    session: requests.Session | None = None,
    *,
    raise_on_error: bool = False,
) -> list[TVMazeShow]:
    if not is_available():
        return []
    session = session or _session()
    try:
        resp = session.get("https://api.tvmaze.com/search/shows", params={"q": query}, timeout=(5, 15))
        if resp.status_code in {403, 429}:
            _set_unavailable("TVMaze lookups are unavailable (HTTP 403/429).")
            return []
        resp.raise_for_status()
    except requests.RequestException:
        _set_unavailable("TVMaze lookups are unavailable (network error).")
        if raise_on_error:
            raise
        return []
    _rate_limit()
    return parse_show_results(resp.json())


def fetch_episodes(
    show_id: int,
    session: requests.Session | None = None,
    timeout: tuple[int, int] = (5, 15),
) -> list[TVMazeEpisode]:
    if not is_available():
        return []
    session = session or _session()
    try:
        resp = session.get(f"https://api.tvmaze.com/shows/{show_id}/episodes", timeout=timeout)
        if resp.status_code in {403, 429}:
            _set_unavailable("TVMaze lookups are unavailable (HTTP 403/429).")
            return []
        resp.raise_for_status()
    except requests.RequestException:
        _set_unavailable("TVMaze lookups are unavailable (network error).")
        return []
    _rate_limit()
    return parse_episode_results(resp.json())


def fetch_show_details(
    show_id: int,
    session: requests.Session | None = None,
    timeout: tuple[int, int] = (5, 15),
) -> TVMazeShowDetails | None:
    if not is_available():
        return None
    session = session or _session()
    try:
        resp = session.get(f"https://api.tvmaze.com/shows/{show_id}", params={"embed": "cast"}, timeout=timeout)
        if resp.status_code in {403, 429}:
            _set_unavailable("TVMaze lookups are unavailable (HTTP 403/429).")
            return None
        resp.raise_for_status()
    except requests.RequestException:
        _set_unavailable("TVMaze lookups are unavailable (network error).")
        return None
    _rate_limit()
    payload = resp.json()
    network = _extract_network_name(payload)
    cast = _parse_cast(payload)
    creator = None
    if not network:
        try:
            crew_resp = session.get(f"https://api.tvmaze.com/shows/{show_id}/crew", timeout=timeout)
            if crew_resp.status_code in {403, 429}:
                _set_unavailable("TVMaze lookups are unavailable (HTTP 403/429).")
                return None
            crew_resp.raise_for_status()
        except requests.RequestException:
            _set_unavailable("TVMaze lookups are unavailable (network error).")
            return None
        _rate_limit()
        creator = _parse_creator(crew_resp.json())
    return TVMazeShowDetails(network=network, creator=creator, cast=cast)
