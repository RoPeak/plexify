from pathlib import Path

from plexify.cache import Cache
from plexify.cache_policy import (
    cache_entry_compatible,
    cache_entry_confirmed_or_auto,
    promote_reusable_with_conflict_tracking,
    reusable_cache_safe,
    should_promote_to_reusable,
)


def test_cache_entry_confirmed_or_auto_respects_ambiguous_flag() -> None:
    assert cache_entry_confirmed_or_auto({"confirmed_by_user": True}) is True
    assert cache_entry_confirmed_or_auto({"selection_mode": "auto", "manual": False}) is True
    assert cache_entry_confirmed_or_auto({"ambiguous": True, "confirmed_by_user": True}) is False


def test_cache_entry_compatible_allows_small_year_gap() -> None:
    assert cache_entry_compatible(2000, 2002) is True
    assert cache_entry_compatible(2000, 2005) is False


def test_reusable_cache_safe_without_year_blocks_ambiguous_titles() -> None:
    assert reusable_cache_safe("The Dark Knight", None) is True
    assert reusable_cache_safe("Show", None) is False
    assert reusable_cache_safe("B1_t00", None) is False
    assert reusable_cache_safe("VTS_01_1", None) is False


def test_should_promote_to_reusable_requires_auto_confident_non_ambiguous() -> None:
    assert (
        should_promote_to_reusable(
            selection_mode="auto",
            manual=False,
            confidence=0.97,
            candidates_count=2,
            top_gap=0.12,
        )
        is True
    )
    assert (
        should_promote_to_reusable(
            selection_mode="confirmed",
            manual=False,
            confidence=1.0,
            candidates_count=1,
            top_gap=0.0,
        )
        is False
    )
    assert (
        should_promote_to_reusable(
            selection_mode="auto",
            manual=False,
            confidence=0.97,
            candidates_count=2,
            top_gap=0.03,
        )
        is False
    )


def test_promote_reusable_with_conflict_tracking_marks_ambiguous(tmp_path: Path) -> None:
    cache = Cache(tmp_path / "cache.json")
    key = "movie|the office|2005"
    first = {"qid": "Q1", "title": "The Office", "year": 2005}
    second = {"qid": "Q2", "title": "The Office", "year": 2005}
    promote_reusable_with_conflict_tracking("movie", cache=cache, key=key, entry=first)
    promote_reusable_with_conflict_tracking("movie", cache=cache, key=key, entry=second)
    entry = cache.get_movie(key)
    assert entry is not None
    assert entry.get("ambiguous") is True
    assert len(entry.get("matches", [])) == 2
