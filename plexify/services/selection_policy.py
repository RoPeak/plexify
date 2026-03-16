from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class CandidatePromptPolicy:
    low_confidence: bool
    risky_reusable_cache_hit: bool
    require_explicit_choice: bool


NO_CANDIDATES_REASON = "no_candidates"
OFFLINE_NO_CACHE_REASON = "offline_no_cache"
MANUAL_SKIP_REASON = "manual_skip"
CONFLICT_SKIP_REASON = "conflict_skip"
FILTERED_MEDIA_TYPE_REASON = "filtered_media_type"


def auto_acceptable(
    candidates: list[Any],
    min_confidence: float,
    *,
    title: str,
    search_query: str,
    target_year: int | None,
    auto_acceptable_fn: Callable[..., bool],
) -> bool:
    return auto_acceptable_fn(
        candidates,
        min_confidence,
        title=title,
        search_query=search_query,
        target_year=target_year,
    )


def build_candidate_prompt_policy(
    *,
    low_confidence: bool,
    risky_reusable_cache_hit: bool,
    allow_risky_enter_accept: bool,
) -> CandidatePromptPolicy:
    return CandidatePromptPolicy(
        low_confidence=low_confidence,
        risky_reusable_cache_hit=risky_reusable_cache_hit,
        require_explicit_choice=(low_confidence or risky_reusable_cache_hit) and not allow_risky_enter_accept,
    )


def no_match_skip_reason(*, offline: bool) -> str:
    return OFFLINE_NO_CACHE_REASON if offline else NO_CANDIDATES_REASON


def folder_show_cache_entry_is_trusted(entry: dict[str, Any] | None) -> bool:
    if not isinstance(entry, dict):
        return False
    if bool(entry.get("confirmed_by_user")):
        return True
    return str(entry.get("selection_mode") or "").strip().lower() == "auto" and not bool(entry.get("manual"))


def should_write_folder_show_cache(*, confirmed_by_user: bool, selection_mode: str | None, manual: bool) -> bool:
    if confirmed_by_user:
        return True
    return (selection_mode or "").strip().lower() == "auto" and not manual


def should_promote_candidate_to_reusable(
    *,
    selection_mode: str | None,
    manual: bool,
    confidence: float,
    candidates_count: int,
    top_gap: float,
    min_confidence: float,
    min_gap: float,
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
