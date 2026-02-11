from __future__ import annotations

import re

from .movie_matcher import title_similarity
from ..util import make_search_query


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

