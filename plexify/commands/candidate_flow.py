from __future__ import annotations

from typing import Any, Callable

from .. import prompting_ui


def select_candidate(
    *,
    media_type: str,
    candidates: list[Any],
    has_more: bool,
    allow_search: bool,
    allow_manual: bool,
    allow_back: bool,
    item: Any | None,
    no_more_results_message: str,
    prompt_base: str,
    prompt_choice: Callable[[str, str], str],
    safe_print: Callable[[str], None],
    print_candidates_fn: Callable[[str, list[Any], Any | None], None],
    allow_enter_accept: bool = True,
) -> Any | None | str:
    return prompting_ui.select_candidate(
        media_type=media_type,
        candidates=candidates,
        has_more=has_more,
        allow_search=allow_search,
        allow_manual=allow_manual,
        allow_back=allow_back,
        item=item,
        no_more_results_message=no_more_results_message,
        prompt_choice=prompt_choice,
        safe_print=safe_print,
        print_candidates_fn=print_candidates_fn,
        prompt_line_fn=lambda has_cands, can_enter, can_search, can_manual, more, can_back: prompting_ui.prompt_line(
            has_candidates=has_cands,
            allow_enter_accept=can_enter,
            allow_search=can_search,
            allow_manual=can_manual,
            has_more=more,
            allow_back=can_back,
            prompt_base=prompt_base,
        ),
        allow_enter_accept=allow_enter_accept,
    )
