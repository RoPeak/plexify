from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..logging_config import get_logger


@dataclass
class _AvailabilityState:
    available: bool = True
    warned: bool = False
    recover_at: float | None = None
    unavailable_reason: str | None = None


_state = _AvailabilityState()
logger = get_logger(__name__)


def _warn_unavailable(message: str) -> None:
    if _state.warned:
        return
    logger.warning(message)
    _state.warned = True


def _set_unavailable(message: str, *, cooldown: float = 60.0) -> None:
    if not _state.available:
        return
    _state.available = False
    _state.recover_at = time.monotonic() + cooldown
    _state.unavailable_reason = message
    _warn_unavailable(message)


def is_available() -> bool:
    if not _state.available and _state.recover_at is not None and time.monotonic() >= _state.recover_at:
        _state.available = True
        _state.recover_at = None
        _state.warned = False
        _state.unavailable_reason = None
    return _state.available


def unavailable_reason() -> str | None:
    return _state.unavailable_reason


def _reset_state() -> None:
    _state.available = True
    _state.warned = False
    _state.recover_at = None
    _state.unavailable_reason = None


def _user_agent() -> str:
    # Wikimedia APIs strongly prefer an informative UA; you can override via env var
    # E.g. `set PLEXIFY_USER_AGENT="plexify/0.1 (contact: your@email)"`
    return (
        os.getenv("PLEXIFY_USER_AGENT")
        or os.getenv("WIKIMEDIA_USER_AGENT")
        or "plexify/0.1 (set PLEXIFY_USER_AGENT to include contact info)"
    )


@dataclass(frozen=True)
class WikidataCandidate:
    qid: str
    label: str
    description: str | None


@dataclass(frozen=True)
class WikidataFilm:
    qid: str
    title: str
    year: int | None
    is_film: bool


FILM_INSTANCE_IDS = frozenset(
    {
        "Q11424",  # film
        "Q202866",  # animated film
        "Q28968258",  # computer-animated film
        "Q120243801",  # direct-to-video film
    }
)


def _session() -> requests.Session:
    session = requests.Session()
    ua = _user_agent()
    session.headers.update(
        {
            "User-Agent": ua,
            # Some Wikimedia services accept/expect Api-User-Agent too
            "Api-User-Agent": ua,
            "Accept": "application/json",
        }
    )
    retries = Retry(total=3, backoff_factor=0.3, status_forcelist=[429, 500, 502, 503, 504])
    session.mount("https://", HTTPAdapter(max_retries=retries))
    return session


def create_session() -> requests.Session:
    return _session()


def _rate_limit(delay: float = 0.25) -> None:
    time.sleep(delay)


def parse_search_results(payload: dict[str, Any]) -> list[WikidataCandidate]:
    results: list[WikidataCandidate] = []
    for item in payload.get("search", []):
        results.append(
            WikidataCandidate(
                qid=str(item.get("id")),
                label=str(item.get("label")),
                description=item.get("description"),
            )
        )
    return results


def _extract_entity_labels(payload: dict[str, Any]) -> dict[str, str]:
    labels: dict[str, str] = {}
    entities = payload.get("entities", {})
    for entity_id, entity in entities.items():
        label = entity.get("labels", {}).get("en", {}).get("value")
        if label:
            labels[entity_id] = str(label)
    return labels


def _extract_year(entity: dict[str, Any]) -> int | None:
    claims = entity.get("claims", {})
    time_claims = claims.get("P577") or []
    preferred_years: list[int] = []
    normal_years: list[int] = []
    for claim in time_claims:
        mainsnak = claim.get("mainsnak", {})
        datavalue = mainsnak.get("datavalue", {})
        value = datavalue.get("value", {})
        time_value = value.get("time")
        if time_value and len(time_value) >= 5:
            try:
                year = int(time_value[1:5])
            except ValueError:
                continue
            if claim.get("rank") == "preferred":
                preferred_years.append(year)
            else:
                normal_years.append(year)
    if preferred_years:
        return min(preferred_years)
    if normal_years:
        return min(normal_years)
    return None


def _extract_claim_ids(entity: dict[str, Any], prop: str) -> list[str]:
    claims = entity.get("claims", {})
    prop_claims = claims.get(prop) or []
    ids: list[str] = []
    for claim in prop_claims:
        mainsnak = claim.get("mainsnak", {})
        datavalue = mainsnak.get("datavalue", {})
        value = datavalue.get("value", {})
        entity_id = value.get("id")
        if entity_id:
            ids.append(str(entity_id))
    return ids


def _extract_description(entity: dict[str, Any]) -> str | None:
    return entity.get("descriptions", {}).get("en", {}).get("value")


def _extract_instance_ids(entity: dict[str, Any]) -> list[str]:
    return _extract_claim_ids(entity, "P31")


def _is_film(entity: dict[str, Any]) -> bool:
    for instance_id in _extract_instance_ids(entity):
        if instance_id in FILM_INSTANCE_IDS:
            return True
    return False


def parse_entity(qid: str, payload: dict[str, Any]) -> WikidataFilm:
    entities = payload.get("entities", {})
    entity = entities.get(qid, {})
    labels = entity.get("labels", {})
    label = labels.get("en", {}).get("value") or qid
    return WikidataFilm(qid=qid, title=str(label), year=_extract_year(entity), is_film=_is_film(entity))


def search(
    query: str,
    session: requests.Session | None = None,
    limit: int = 10,
    *,
    raise_on_error: bool = False,
) -> list[WikidataCandidate]:
    if not is_available():
        return []
    session = session or _session()
    try:
        resp = session.get(
            "https://www.wikidata.org/w/api.php",
            params={
                "action": "wbsearchentities",
                "search": query,
                "language": "en",
                "format": "json",
                "limit": limit,
                "type": "item",
            },
            timeout=(5, 15),
        )
        if resp.status_code in {403, 429}:
            _set_unavailable("Wikidata lookups are unavailable (HTTP 403/429).")
            return []
        resp.raise_for_status()
    except requests.RequestException:
        _set_unavailable("Wikidata lookups are unavailable (network error).")
        if raise_on_error:
            raise
        return []
    _rate_limit()
    return parse_search_results(resp.json())


def fetch_labels(ids: list[str], session: requests.Session | None = None) -> dict[str, str]:
    if not ids or not is_available():
        return {}
    session = session or _session()
    try:
        resp = session.get(
            "https://www.wikidata.org/w/api.php",
            params={
                "action": "wbgetentities",
                "ids": "|".join(ids),
                "props": "labels",
                "languages": "en",
                "format": "json",
            },
            timeout=(5, 15),
        )
        if resp.status_code in {403, 429}:
            _set_unavailable("Wikidata lookups are unavailable (HTTP 403/429).")
            return {}
        resp.raise_for_status()
    except requests.RequestException:
        _set_unavailable("Wikidata lookups are unavailable (network error).")
        return {}
    _rate_limit()
    return _extract_entity_labels(resp.json())


def _extract_director_from_description(description: str | None) -> str | None:
    if not description:
        return None
    match = re.search(r"directed by ([^,.;]+)", description, flags=re.IGNORECASE)
    if not match:
        return None
    return match.group(1).strip()


def fetch_enrichment(
    qid: str,
    session: requests.Session | None = None,
    timeout: tuple[int, int] = (5, 15),
) -> dict[str, Any] | None:
    if not is_available():
        return None
    session = session or _session()
    try:
        resp = session.get(f"https://www.wikidata.org/wiki/Special:EntityData/{qid}.json", timeout=timeout)
        if resp.status_code in {403, 429}:
            _set_unavailable("Wikidata lookups are unavailable (HTTP 403/429).")
            return None
        resp.raise_for_status()
    except requests.RequestException:
        _set_unavailable("Wikidata lookups are unavailable (network error).")
        return None
    _rate_limit()
    payload = resp.json()
    entity = payload.get("entities", {}).get(qid, {})
    director_ids = _extract_claim_ids(entity, "P57")
    cast_ids = _extract_claim_ids(entity, "P161")
    description = _extract_description(entity)
    ids = []
    if director_ids:
        ids.extend(director_ids[:1])
    if cast_ids:
        ids.extend(cast_ids[:3])
    labels = fetch_labels(ids, session=session) if ids else {}
    director = None
    if director_ids:
        director = labels.get(director_ids[0])
    if not director:
        director = _extract_director_from_description(description)
    cast = []
    for cast_id in cast_ids[:3]:
        label = labels.get(cast_id)
        if label:
            cast.append(label)
    return {
        "director": director,
        "cast": cast,
        "description": description,
    }


def fetch_entity(qid: str, session: requests.Session | None = None) -> WikidataFilm:
    if not is_available():
        return WikidataFilm(qid=qid, title=qid, year=None, is_film=False)
    session = session or _session()
    try:
        resp = session.get(f"https://www.wikidata.org/wiki/Special:EntityData/{qid}.json", timeout=(5, 15))
        if resp.status_code in {403, 429}:
            _set_unavailable("Wikidata lookups are unavailable (HTTP 403/429).")
            return WikidataFilm(qid=qid, title=qid, year=None, is_film=False)
        resp.raise_for_status()
    except requests.RequestException:
        _set_unavailable("Wikidata lookups are unavailable (network error).")
        return WikidataFilm(qid=qid, title=qid, year=None, is_film=False)
    _rate_limit()
    return parse_entity(qid, resp.json())
