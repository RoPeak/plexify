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


def test_ui_candidate_page_accepts_lookup_diagnostics() -> None:
    page = UICandidatePage(
        candidates=[],
        raw_results=[],
        next_offset=0,
        has_more=False,
        provider="Wikidata",
        lookup_status="provider_unavailable",
        lookup_reason="Wikidata lookups are unavailable (HTTP 403/429).",
        raw_result_count=0,
        candidate_count=0,
        filtered_count=0,
        search_time=0.5,
    )

    assert page.provider == "Wikidata"
    assert page.lookup_status == "provider_unavailable"
    assert page.lookup_reason
    assert page.raw_result_count == 0
