from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


_available = True
_warned = False


def _warn_unavailable(message: str) -> None:
    global _warned
    if _warned:
        return
    print(message)
    _warned = True


def _set_unavailable(message: str) -> None:
    global _available
    if not _available:
        return
    _available = False
    _warn_unavailable(message)


def is_available() -> bool:
    return _available


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


def _extract_year(entity: dict[str, Any]) -> int | None:
    claims = entity.get("claims", {})
    time_claims = claims.get("P577") or []
    for claim in time_claims:
        mainsnak = claim.get("mainsnak", {})
        datavalue = mainsnak.get("datavalue", {})
        value = datavalue.get("value", {})
        time_value = value.get("time")
        if time_value and len(time_value) >= 5:
            try:
                return int(time_value[1:5])
            except ValueError:
                continue
    return None


def _is_film(entity: dict[str, Any]) -> bool:
    claims = entity.get("claims", {})
    instance = claims.get("P31") or []
    for claim in instance:
        mainsnak = claim.get("mainsnak", {})
        datavalue = mainsnak.get("datavalue", {})
        value = datavalue.get("value", {})
        if value.get("id") == "Q11424":
            return True
    return False


def parse_entity(qid: str, payload: dict[str, Any]) -> WikidataFilm:
    entities = payload.get("entities", {})
    entity = entities.get(qid, {})
    labels = entity.get("labels", {})
    label = labels.get("en", {}).get("value") or qid
    return WikidataFilm(qid=qid, title=str(label), year=_extract_year(entity), is_film=_is_film(entity))


def search(query: str, session: requests.Session | None = None) -> list[WikidataCandidate]:
    if not _available:
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
                "limit": 10,
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
        return []
    _rate_limit()
    return parse_search_results(resp.json())


def fetch_entity(qid: str, session: requests.Session | None = None) -> WikidataFilm:
    if not _available:
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
