from __future__ import annotations

from plexify.ui_services import UICandidatePage


def test_ui_candidate_page_accepts_attempted_queries() -> None:
    page = UICandidatePage(
        candidates=[],
        raw_results=[],
        next_offset=0,
        has_more=False,
        attempted_queries=["first query", "fallback query"],
    )

    assert page.attempted_queries == ["first query", "fallback query"]
