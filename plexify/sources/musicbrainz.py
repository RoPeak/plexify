from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .. import __version__
from ..logging_config import get_logger


BASE_URL = "https://musicbrainz.org/ws/2"
_available = True
_warned = False
_warned_user_agent = False
_last_request = 0.0
_unavailable_reason: str | None = None
_recover_at: float | None = None
logger = get_logger(__name__)


def _warn_unavailable(message: str) -> None:
    global _warned
    if _warned:
        return
    _warned = True
    logger.warning(message)


def _set_unavailable(message: str, *, cooldown: float = 60.0) -> None:
    global _available, _recover_at
    if not _available:
        return
    _available = False
    _recover_at = time.monotonic() + cooldown
    global _unavailable_reason
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
class ReleaseCandidate:
    mbid: str
    title: str
    artist: str
    year: int | None
    country: str | None
    score: float
    track_count: int | None
    raw_score: float | None = None
    requested_track_count: int | None = None


@dataclass(frozen=True)
class Track:
    number: int
    title: str
    disc: int


def _session() -> requests.Session:
    session = requests.Session()
    retries = Retry(total=3, backoff_factor=0.3, status_forcelist=[429, 500, 502, 503, 504])
    session.mount("https://", HTTPAdapter(max_retries=retries))
    user_agent = os.environ.get("PLEXIFY_USER_AGENT")
    if not user_agent:
        global _warned_user_agent
        if not _warned_user_agent:
            logger.warning("MusicBrainz: set PLEXIFY_USER_AGENT with contact info to avoid throttling.")
            _warned_user_agent = True
        user_agent = f"plexify/{__version__} (contact: set PLEXIFY_USER_AGENT)"
    session.headers.update(
        {
            "User-Agent": user_agent,
        }
    )
    return session


def create_session() -> requests.Session:
    return _session()


def _rate_limit(delay: float = 1.0) -> None:
    global _last_request
    now = time.monotonic()
    elapsed = now - _last_request
    if elapsed < delay:
        time.sleep(delay - elapsed)
    _last_request = time.monotonic()


def _extract_year(value: str | None) -> int | None:
    if not value:
        return None
    match = re.match(r"(\d{4})", value)
    if match:
        return int(match.group(1))
    return None


def _parse_artist_credit(credit: list[dict[str, Any]] | None) -> str:
    if not credit:
        return "Unknown"
    names: list[str] = []
    for entry in credit:
        name = entry.get("name")
        if name:
            names.append(str(name))
    return " ".join(names).strip() or "Unknown"


def _release_query(session: requests.Session, query: str, limit: int) -> list[dict[str, Any]]:
    if not is_available():
        return []
    try:
        _rate_limit()
        resp = session.get(
            f"{BASE_URL}/release",
            params={"query": query, "fmt": "json", "limit": limit},
            timeout=(5, 15),
        )
        if resp.status_code in {403, 429, 503}:
            _set_unavailable("MusicBrainz lookups are unavailable (HTTP 403/429/503).")
            return []
        resp.raise_for_status()
    except requests.RequestException:
        _set_unavailable("MusicBrainz lookups are unavailable (network error).")
        return []
    payload = resp.json()
    releases = payload.get("releases", [])
    return releases if isinstance(releases, list) else []


def _parse_release_candidate(item: dict[str, Any]) -> ReleaseCandidate | None:
    if not item:
        return None
    mbid = item.get("id")
    title = item.get("title")
    if not mbid or not title:
        return None
    year = _extract_year(item.get("date") or item.get("first-release-date"))
    country = item.get("country")
    score_raw = item.get("score") or 0
    try:
        score = float(score_raw) / 100.0
    except (TypeError, ValueError):
        score = 0.0
    artist_name = _parse_artist_credit(item.get("artist-credit"))
    track_count = item.get("track-count")
    if isinstance(track_count, int):
        track_count_val = track_count
    elif isinstance(track_count, str) and track_count.isdigit():
        track_count_val = int(track_count)
    else:
        track_count_val = None
    return ReleaseCandidate(
        mbid=str(mbid),
        title=str(title),
        artist=artist_name,
        year=year,
        country=str(country) if country else None,
        score=score,
        track_count=track_count_val,
        raw_score=score,
    )


def search_releases(
    artist: str,
    album: str,
    limit: int = 8,
    session: requests.Session | None = None,
    year: int | None = None,
) -> list[ReleaseCandidate]:
    if not is_available():
        return []
    session = session or _session()
    artist_text = artist.strip()
    album_text = album.strip()
    queries: list[str] = []
    if artist_text and album_text and year:
        queries.append(f'artist:"{artist_text}" AND release:"{album_text}" AND date:{year}')
    if artist_text and album_text:
        queries.append(f'artist:"{artist_text}" AND release:"{album_text}"')
    if album_text:
        queries.append(f'release:"{album_text}"')

    candidates_by_mbid: dict[str, ReleaseCandidate] = {}
    for query in queries:
        for item in _release_query(session, query, limit):
            candidate = _parse_release_candidate(item)
            if candidate is None:
                continue
            existing = candidates_by_mbid.get(candidate.mbid)
            if existing is None or candidate.score > existing.score:
                candidates_by_mbid[candidate.mbid] = candidate
        if len(candidates_by_mbid) >= limit:
            break

    results: list[ReleaseCandidate] = []
    for candidate in candidates_by_mbid.values():
        results.append(candidate)
    results.sort(
        key=lambda cand: (
            -(cand.raw_score if cand.raw_score is not None else cand.score),
            cand.title.casefold(),
        )
    )
    return results[:limit]


def _parse_track_number(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    text = str(value)
    if text.isdigit():
        return int(text)
    match = re.match(r"(\d+)", text)
    if match:
        return int(match.group(1))
    return None


def fetch_release_tracks(mbid: str, session: requests.Session | None = None) -> list[Track]:
    if not is_available():
        return []
    session = session or _session()
    try:
        _rate_limit()
        resp = session.get(
            f"{BASE_URL}/release/{mbid}",
            params={"inc": "recordings", "fmt": "json"},
            timeout=(5, 15),
        )
        if resp.status_code in {403, 429, 503}:
            _set_unavailable("MusicBrainz lookups are unavailable (HTTP 403/429/503).")
            return []
        resp.raise_for_status()
    except requests.RequestException:
        _set_unavailable("MusicBrainz lookups are unavailable (network error).")
        return []
    payload = resp.json()
    results: list[Track] = []
    for media in payload.get("media", []):
        disc_number = media.get("position") or 1
        tracks = media.get("tracks") or []
        for track in tracks:
            title = track.get("title") or (track.get("recording") or {}).get("title")
            number = _parse_track_number(track.get("position") or track.get("number"))
            if not title or number is None:
                continue
            results.append(Track(number=int(number), title=str(title), disc=int(disc_number)))
    return results
