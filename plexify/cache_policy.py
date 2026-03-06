from __future__ import annotations

import re
from typing import Any

from .cache import Cache
from .util import normalize_title_for_similarity, now_timestamp

GENERIC_RIP_TITLE_RE = re.compile(
    r"^(?:"
    r"[a-z]\d+\s*t\d+"
    r"|vts\s*\d+(?:\s+\d+)?"
    r"|disc\s*\d+"
    r"|track\s*\d+"
    r")$",
    re.IGNORECASE,
)


def cache_entry_confirmed_or_auto(entry: dict[str, Any] | None) -> bool:
    if not entry:
        return False
    if bool(entry.get("ambiguous")):
        return False
    if bool(entry.get("confirmed_by_user")):
        return True
    if entry.get("selection_mode") == "auto" and not bool(entry.get("manual")):
        return True
    return False


def cache_entry_compatible(inferred_year: int | None, cached_year: int | None) -> bool:
    if inferred_year is None or cached_year is None:
        return True
    return year_distance(inferred_year, cached_year) <= 2


def year_distance(target_year: int | None, candidate_year: int | None) -> int:
    if not target_year or not candidate_year:
        return 999
    return abs(target_year - candidate_year)


def is_ambiguous_cache_title(title: str) -> bool:
    normalised = normalize_title_for_similarity(title)
    compact = re.sub(r"[\s._-]+", "", normalised)
    if compact and GENERIC_RIP_TITLE_RE.fullmatch(normalised):
        return True
    if compact and re.fullmatch(r"[a-z]\d+t\d+", compact):
        return True
    if compact and re.fullmatch(r"vts\d+(?:\d+)?", compact):
        return True
    if compact and re.fullmatch(r"(?:disc|track)\d+", compact):
        return True
    tokens = [token for token in re.split(r"\s+", normalised) if token]
    if not tokens:
        return True
    if len(tokens) == 1:
        return True
    if len(normalised) < 6:
        return True
    generic_titles = {"movie", "film", "show", "series", "episode", "unknown", "sample", "video", "tv"}
    if normalised in generic_titles:
        return True
    return False


def reusable_cache_safe(title: str, year: int | None) -> bool:
    if year is not None:
        return True
    return not is_ambiguous_cache_title(title)


def reusable_match_from_entry(media_type: str, entry: dict[str, Any] | None) -> dict[str, Any] | None:
    if not entry:
        return None
    if media_type == "movie":
        qid = entry.get("qid")
        if not qid:
            return None
        return {"id": str(qid), "title": entry.get("title"), "year": entry.get("year")}
    show_id = entry.get("id")
    if not show_id:
        return None
    return {"id": str(show_id), "title": entry.get("name"), "year": entry.get("premiered")}


def reusable_matches_conflict(
    media_type: str,
    existing_entry: dict[str, Any] | None,
    new_entry: dict[str, Any],
) -> bool:
    new_match = reusable_match_from_entry(media_type, new_entry)
    if new_match is None:
        return False
    if existing_entry and bool(existing_entry.get("ambiguous")):
        matches = existing_entry.get("matches")
        if not isinstance(matches, list):
            return False
        for match in matches:
            if isinstance(match, dict) and str(match.get("id")) == str(new_match["id"]):
                return False
        return True
    existing_match = reusable_match_from_entry(media_type, existing_entry)
    if existing_match is None:
        return False
    return str(existing_match["id"]) != str(new_match["id"])


def promote_reusable_with_conflict_tracking(
    media_type: str,
    *,
    cache: Cache,
    key: str,
    entry: dict[str, Any],
) -> None:
    existing = cache.get_movie(key) if media_type == "movie" else cache.get_show(key)
    if not reusable_matches_conflict(media_type, existing, entry):
        if media_type == "movie":
            cache.set_movie(key, entry)
        else:
            cache.set_show(key, entry)
        return

    new_match = reusable_match_from_entry(media_type, entry)
    matches: list[dict[str, Any]] = []
    if existing and bool(existing.get("ambiguous")) and isinstance(existing.get("matches"), list):
        for row in existing["matches"]:
            if isinstance(row, dict) and row.get("id") is not None:
                matches.append({"id": str(row.get("id")), "title": row.get("title"), "year": row.get("year")})
    else:
        existing_match = reusable_match_from_entry(media_type, existing)
        if existing_match is not None:
            matches.append(existing_match)
    if new_match is not None and all(str(row.get("id")) != str(new_match["id"]) for row in matches):
        matches.append(new_match)

    conflict_entry = {
        "ambiguous": True,
        "matches": matches,
        "selection_mode": "ambiguous",
        "created_at": now_timestamp(),
    }
    if media_type == "movie":
        cache.set_movie(key, conflict_entry)
    else:
        cache.set_show(key, conflict_entry)


def should_promote_to_reusable(
    *,
    selection_mode: str | None,
    manual: bool,
    confidence: float,
    candidates_count: int,
    top_gap: float,
    min_confidence: float = 0.95,
    min_gap: float = 0.10,
) -> bool:
    if selection_mode != "auto":
        return False
    if manual:
        return False
    if confidence < min_confidence:
        return False
    if candidates_count <= 1:
        return True
    return top_gap >= min_gap
