from __future__ import annotations

from ..cache_policy import is_ambiguous_cache_title
from ..infer import InferredItem


def reusable_cache_hit_looks_risky(item: InferredItem, top_confidence: float, min_confidence: float) -> bool:
    if is_ambiguous_cache_title(item.title):
        return True
    if item.year is None:
        return True
    return top_confidence < min_confidence
