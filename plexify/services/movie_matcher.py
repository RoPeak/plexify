from __future__ import annotations

import re

from rapidfuzz import fuzz

from ..cache_policy import year_distance
from ..util import make_search_query, normalize_title_for_similarity


def compact_text(value: str) -> str:
    return re.sub(r"\s+", "", value)


def compact_sequel_form(value: str) -> str | None:
    tokens = value.split()
    if len(tokens) < 2:
        return None
    if len(tokens[0]) == 1 and tokens[-1].isdigit():
        return f"{tokens[0]}{tokens[-1]}"
    return None


def title_similarity(title_guess: str, title_actual: str) -> float:
    norm_left = normalize_title_for_similarity(title_guess) or title_guess.lower()
    norm_right = normalize_title_for_similarity(title_actual) or title_actual.lower()
    search_left = make_search_query(title_guess) or title_guess.lower()
    search_right = make_search_query(title_actual) or title_actual.lower()
    forms_left = {norm_left, compact_text(norm_left), search_left, compact_text(search_left)}
    forms_right = {norm_right, compact_text(norm_right), search_right, compact_text(search_right)}
    compact_left = compact_sequel_form(norm_left)
    compact_right = compact_sequel_form(norm_right)
    if compact_left:
        forms_left.add(compact_left)
    if compact_right:
        forms_right.add(compact_right)
    best = 0.0
    for left in forms_left:
        for right in forms_right:
            score = max(
                fuzz.WRatio(left, right),
                fuzz.partial_ratio(left, right),
            ) / 100.0
            if score > best:
                best = score
    return best


def year_adjustment(target_year: int | None, candidate_year: int | None) -> float:
    if not target_year or not candidate_year:
        return 0.0
    diff = abs(target_year - candidate_year)
    if diff == 0:
        return 0.20
    if diff == 1:
        return 0.08
    if diff == 2:
        return 0.04
    return -min(0.30, 0.03 * diff)


def confidence_score(title_guess: str, title_actual: str, year_guess: int | None, year_actual: int | None) -> float:
    base = title_similarity(title_guess, title_actual)
    adjusted = base + year_adjustment(year_guess, year_actual)
    return max(0.0, min(1.0, adjusted))


def has_sequel_marker(title: str) -> bool:
    tokens = re.split(r"[.\s_\-:/\\]+", title.strip())
    if not tokens:
        return False
    last = tokens[-1].lower()
    if last in {"i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x", "xi", "xii", "xiii", "xiv", "xv"}:
        return True
    return bool(re.fullmatch(r"\d+", last))


def search_lost_sequel_marker(title: str, search_query: str) -> bool:
    if not has_sequel_marker(title):
        return False
    return not has_sequel_marker(search_query)


def auto_acceptable(
    *,
    top_confidence: float,
    second_confidence: float | None,
    top_year: int | None,
    min_confidence: float,
    title: str,
    search_query: str,
    target_year: int | None,
    min_gap: float = 0.08,
) -> bool:
    if top_confidence < min_confidence:
        return False
    if search_lost_sequel_marker(title, search_query):
        return False
    if second_confidence is None:
        return True
    gap = top_confidence - second_confidence
    if gap >= min_gap:
        return True
    if year_distance(target_year, top_year) <= 2:
        return True
    return False
