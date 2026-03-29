from __future__ import annotations

import re

from .movie_matcher import title_similarity
from ..util import make_search_query

_SIGNIFICANT_QUERY_STOPWORDS = {
    "a",
    "an",
    "and",
    "for",
    "from",
    "in",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
}


def tv_confidence_score(title_guess: str, title_actual: str, year_guess: int | None, year_actual: int | None) -> float:
    base = title_similarity(title_guess, title_actual)
    if not year_guess or not year_actual:
        return max(0.0, min(1.0, base))
    diff = abs(year_guess - year_actual)
    if diff == 0:
        adjustment = 0.35
    elif diff <= 1:
        adjustment = 0.18
    elif diff <= 2:
        adjustment = 0.10
    elif diff <= 5:
        adjustment = -0.08 * diff
    elif diff <= 10:
        adjustment = -0.35
    else:
        adjustment = -0.6
    return max(0.0, min(1.0, base + adjustment))


def normalize_tv_retry_query(value: str, explicit_season_re: re.Pattern[str]) -> str:
    cleaned = explicit_season_re.sub(" ", value or "")
    return make_search_query(cleaned) or cleaned.strip()


def _significant_query_tokens(value: str) -> set[str]:
    normalized = make_search_query(value) or value.lower()
    return {
        token
        for token in normalized.split()
        if token and token not in _SIGNIFICANT_QUERY_STOPWORDS and not token.isdigit()
    }


def search_lost_title_tokens(title: str, search_query: str) -> bool:
    title_tokens = _significant_query_tokens(title)
    query_tokens = _significant_query_tokens(search_query)
    if not title_tokens or not query_tokens:
        return False
    return bool(title_tokens - query_tokens)


def broadened_search_query(title: str, search_query: str) -> bool:
    return search_lost_title_tokens(title, search_query)
