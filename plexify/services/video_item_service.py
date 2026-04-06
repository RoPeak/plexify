from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
from rich.progress import Progress


@dataclass
class _CandidateLoopState:
    item: Any
    reference_title: str
    search_query: str
    query_history: list[str]
    page: Any
    candidates: list[Any]
    raw_results: Any
    next_offset: int
    has_more: bool
    search_refined: bool = False


def _merge_query_history(*query_groups: list[str] | None) -> list[str]:
    seen: set[str] = set()
    merged: list[str] = []
    for group in query_groups:
        if not group:
            continue
        for query in group:
            compact = " ".join(str(query).split()).strip()
            if not compact:
                continue
            marker = compact.casefold()
            if marker in seen:
                continue
            seen.add(marker)
            merged.append(compact)
    return merged


def _state_query_history(state: _CandidateLoopState, page: Any) -> list[str]:
    return _merge_query_history(state.query_history, getattr(page, "attempted_queries", None))


def _announce_query_history(state: _CandidateLoopState, helpers: Any, progress: Progress | None) -> None:
    helpers._announce_attempted_queries(state.query_history, progress)


def _prepare_item_context(
    *,
    item: Any,
    cache: Any,
    incoming_root: Path | None,
    media_type_overrides: dict[str, str] | None,
    helpers: Any,
) -> tuple[Any, str | None, str | None]:
    item, override_key = helpers._resolve_media_type_override(item, cache, incoming_root, media_type_overrides)
    folder_show_key = helpers.tv_show_folder_cache_key(item.path, incoming_root) if item.media_type == "tv" else None
    item = helpers._apply_tv_folder_season_lock(item, cache, folder_show_key)
    return item, override_key, folder_show_key


def _load_tv_candidate_page(
    *,
    item: Any,
    session_tv: requests.Session,
    cache: Any,
    show_cache: bool,
    incoming_root: Path | None,
    cache_key: str,
    next_offset: int,
    raw_results_tv: list[Any] | None,
    search_query: str,
    progress: Progress | None,
    offline: bool,
    interactive: bool,
    tv_search_cache: dict[str, list[Any]] | None,
    helpers: Any,
) -> Any:
    return helpers._fetch_with_retry(
        "TVMaze",
        lambda: helpers._tv_candidates(
            item,
            session_tv,
            cache,
            show_cache,
            incoming_root=incoming_root,
            cache_key=cache_key,
            offset=next_offset,
            raw_results=raw_results_tv,
            search_query=search_query,
            progress=progress,
            offline=offline,
            interactive=interactive,
            search_cache=tv_search_cache,
        ),
        interactive,
        progress,
    )


def _load_movie_candidate_page(
    *,
    item: Any,
    session_wd: requests.Session,
    cache: Any,
    show_cache: bool,
    cache_key: str,
    next_offset: int,
    raw_results_movie: list[Any] | None,
    search_query: str,
    progress: Progress | None,
    limit: int,
    offline: bool,
    interactive: bool,
    movie_entity_cache: dict[str, Any] | None,
    helpers: Any,
) -> Any:
    return helpers._fetch_with_retry(
        "Wikidata",
        lambda: helpers._movie_candidates(
            item,
            session_wd,
            cache,
            show_cache,
            cache_key=cache_key,
            offset=next_offset,
            raw_results=raw_results_movie,
            search_query=search_query,
            progress=progress,
            limit=limit,
            offline=offline,
            interactive=interactive,
            movie_entity_cache=movie_entity_cache,
        ),
        interactive,
        progress,
    )


def _apply_candidate_page(page: Any, stats: Any, helpers: Any) -> tuple[list[Any], Any, int, bool]:
    if page.cache_hit:
        helpers._record_cache_hit(stats)
    return page.candidates, page.raw_results, page.next_offset, page.has_more


def _reload_tv_candidate_state(
    *,
    item: Any,
    session_tv: requests.Session,
    cache: Any,
    show_cache: bool,
    incoming_root: Path | None,
    cache_key: str,
    search_query: str,
    progress: Progress | None,
    offline: bool,
    interactive: bool,
    tv_search_cache: dict[str, list[Any]] | None,
    stats: Any,
    helpers: Any,
) -> tuple[Any | None, list[Any], Any, int, bool]:
    page = _load_tv_candidate_page(
        item=item,
        session_tv=session_tv,
        cache=cache,
        show_cache=show_cache,
        incoming_root=incoming_root,
        cache_key=cache_key,
        next_offset=0,
        raw_results_tv=None,
        search_query=search_query,
        progress=progress,
        offline=offline,
        interactive=interactive,
        tv_search_cache=tv_search_cache,
        helpers=helpers,
    )
    if page is None:
        return None, [], None, 0, False
    candidates, raw_results_tv, next_offset, has_more = _apply_candidate_page(page, stats, helpers)
    return page, candidates, raw_results_tv, next_offset, has_more


def _reload_movie_candidate_state(
    *,
    item: Any,
    session_wd: requests.Session,
    cache: Any,
    show_cache: bool,
    cache_key: str,
    search_query: str,
    progress: Progress | None,
    offline: bool,
    interactive: bool,
    movie_entity_cache: dict[str, Any] | None,
    stats: Any,
    helpers: Any,
) -> tuple[Any | None, list[Any], Any, int, bool]:
    page = _load_movie_candidate_page(
        item=item,
        session_wd=session_wd,
        cache=cache,
        show_cache=show_cache,
        cache_key=cache_key,
        next_offset=0,
        raw_results_movie=None,
        search_query=search_query,
        progress=progress,
        limit=5,
        offline=offline,
        interactive=interactive,
        movie_entity_cache=movie_entity_cache,
        helpers=helpers,
    )
    if page is None:
        return None, [], None, 0, False
    candidates, raw_results_movie, next_offset, has_more = _apply_candidate_page(page, stats, helpers)
    return page, candidates, raw_results_movie, next_offset, has_more


def _inline_search_query(choice: Any) -> str | None:
    if isinstance(choice, str) and choice.startswith("search:"):
        query = choice.split("search:", 1)[1].strip()
        if query:
            return query
    return None


def _reload_tv_loop_state(
    *,
    state: _CandidateLoopState,
    session_tv: requests.Session,
    cache: Any,
    show_cache: bool,
    incoming_root: Path | None,
    cache_key: str,
    progress: Progress | None,
    offline: bool,
    interactive: bool,
    tv_search_cache: dict[str, list[Any]] | None,
    stats: Any,
    helpers: Any,
) -> _CandidateLoopState | None:
    page, candidates, raw_results_tv, next_offset, has_more = _reload_tv_candidate_state(
        item=state.item,
        session_tv=session_tv,
        cache=cache,
        show_cache=show_cache,
        incoming_root=incoming_root,
        cache_key=cache_key,
        search_query=state.search_query,
        progress=progress,
        offline=offline,
        interactive=interactive,
        tv_search_cache=tv_search_cache,
        stats=stats,
        helpers=helpers,
    )
    if page is None:
        return None
    return _CandidateLoopState(
        item=state.item,
        reference_title=state.reference_title,
        search_query=page.search_query_used or state.search_query,
        query_history=_state_query_history(state, page),
        page=page,
        candidates=candidates,
        raw_results=raw_results_tv,
        next_offset=next_offset,
        has_more=has_more,
        search_refined=state.search_refined,
    )


def _advance_tv_loop_state(
    *,
    state: _CandidateLoopState,
    session_tv: requests.Session,
    cache: Any,
    show_cache: bool,
    incoming_root: Path | None,
    cache_key: str,
    progress: Progress | None,
    offline: bool,
    interactive: bool,
    tv_search_cache: dict[str, list[Any]] | None,
    stats: Any,
    helpers: Any,
) -> _CandidateLoopState | None:
    page = _load_tv_candidate_page(
        item=state.item,
        session_tv=session_tv,
        cache=cache,
        show_cache=show_cache,
        incoming_root=incoming_root,
        cache_key=cache_key,
        next_offset=state.next_offset,
        raw_results_tv=state.raw_results,
        search_query=state.search_query,
        progress=progress,
        offline=offline,
        interactive=interactive,
        tv_search_cache=tv_search_cache,
        helpers=helpers,
    )
    if page is None:
        return None
    candidates, raw_results_tv, next_offset, has_more = _apply_candidate_page(page, stats, helpers)
    return _CandidateLoopState(
        item=state.item,
        reference_title=state.reference_title,
        search_query=page.search_query_used or state.search_query,
        query_history=_state_query_history(state, page),
        page=page,
        candidates=candidates,
        raw_results=raw_results_tv,
        next_offset=next_offset,
        has_more=has_more,
        search_refined=state.search_refined,
    )


def _reload_movie_loop_state(
    *,
    state: _CandidateLoopState,
    session_wd: requests.Session,
    cache: Any,
    show_cache: bool,
    cache_key: str,
    progress: Progress | None,
    offline: bool,
    interactive: bool,
    movie_entity_cache: dict[str, Any] | None,
    stats: Any,
    helpers: Any,
) -> _CandidateLoopState | None:
    page, candidates, raw_results_movie, next_offset, has_more = _reload_movie_candidate_state(
        item=state.item,
        session_wd=session_wd,
        cache=cache,
        show_cache=show_cache,
        cache_key=cache_key,
        search_query=state.search_query,
        progress=progress,
        offline=offline,
        interactive=interactive,
        movie_entity_cache=movie_entity_cache,
        stats=stats,
        helpers=helpers,
    )
    if page is None:
        return None
    return _CandidateLoopState(
        item=state.item,
        reference_title=state.reference_title,
        search_query=page.search_query_used or state.search_query,
        query_history=_state_query_history(state, page),
        page=page,
        candidates=candidates,
        raw_results=raw_results_movie,
        next_offset=next_offset,
        has_more=has_more,
        search_refined=state.search_refined,
    )


def _advance_movie_loop_state(
    *,
    state: _CandidateLoopState,
    session_wd: requests.Session,
    cache: Any,
    show_cache: bool,
    cache_key: str,
    progress: Progress | None,
    offline: bool,
    interactive: bool,
    movie_entity_cache: dict[str, Any] | None,
    stats: Any,
    helpers: Any,
) -> _CandidateLoopState | None:
    page = _load_movie_candidate_page(
        item=state.item,
        session_wd=session_wd,
        cache=cache,
        show_cache=show_cache,
        cache_key=cache_key,
        next_offset=state.next_offset,
        raw_results_movie=state.raw_results,
        search_query=state.search_query,
        progress=progress,
        limit=5,
        offline=offline,
        interactive=interactive,
        movie_entity_cache=movie_entity_cache,
        helpers=helpers,
    )
    if page is None:
        return None
    candidates, raw_results_movie, next_offset, has_more = _apply_candidate_page(page, stats, helpers)
    return _CandidateLoopState(
        item=state.item,
        reference_title=state.reference_title,
        search_query=page.search_query_used or state.search_query,
        query_history=_state_query_history(state, page),
        page=page,
        candidates=candidates,
        raw_results=raw_results_movie,
        next_offset=next_offset,
        has_more=has_more,
        search_refined=state.search_refined,
    )


def _reprocess_with_media_type(
    *,
    new_media_type: str,
    item: Any,
    library: Path,
    cache: Any,
    mode: str,
    copy_mode: bool,
    interactive: bool,
    auto_accept: bool,
    min_confidence: float,
    session_tv: requests.Session,
    session_wd: requests.Session,
    episode_cache: Any,
    progress: Progress | None,
    show_cache: bool,
    stats: Any,
    incoming_root: Path | None,
    planned: dict[str, int] | None,
    on_conflict: str,
    allow_back: bool,
    offline: bool,
    allow_risky_enter_accept: bool,
    media_type_overrides: dict[str, str] | None,
    tv_search_cache: dict[str, list[Any]] | None,
    movie_entity_cache: dict[str, Any] | None,
    cache_override_key: str | None,
    helpers: Any,
    reprocess_item_fn: Any,
    requested_media_type: str | None,
) -> tuple[Any | None, bool]:
    helpers._persist_media_type_override(cache, cache_override_key, new_media_type, media_type_overrides, progress)
    return reprocess_item_fn(
        item=helpers._switch_item_media_type(item, new_media_type),
        library=library,
        cache=cache,
        mode=mode,
        copy_mode=copy_mode,
        interactive=interactive,
        auto_accept=auto_accept,
        min_confidence=min_confidence,
        session_tv=session_tv,
        session_wd=session_wd,
        episode_cache=episode_cache,
        progress=progress,
        show_cache=show_cache,
        stats=stats,
        incoming_root=incoming_root,
        planned=planned,
        on_conflict=on_conflict,
        allow_back=allow_back,
        offline=offline,
        allow_risky_enter_accept=allow_risky_enter_accept,
        media_type_overrides=media_type_overrides,
        tv_search_cache=tv_search_cache,
        movie_entity_cache=movie_entity_cache,
        requested_media_type=requested_media_type,
    )


def _handle_tv_no_candidates(
    *,
    state: _CandidateLoopState,
    library: Path,
    cache: Any,
    mode: str,
    copy_mode: bool,
    interactive: bool,
    auto_accept: bool,
    min_confidence: float,
    session_tv: requests.Session,
    session_wd: requests.Session,
    episode_cache: Any,
    progress: Progress | None,
    show_cache: bool,
    stats: Any,
    incoming_root: Path | None,
    planned: dict[str, int] | None,
    on_conflict: str,
    allow_back: bool,
    offline: bool,
    allow_risky_enter_accept: bool,
    media_type_overrides: dict[str, str] | None,
    tv_search_cache: dict[str, list[Any]] | None,
    movie_entity_cache: dict[str, Any] | None,
    cache_key: str,
    override_key: str | None,
    requested_media_type: str | None,
    helpers: Any,
    reprocess_item_fn: Any,
) -> tuple[str, Any]:
    if not interactive:
        if offline:
            helpers.log_event(
                helpers.logger,
                "offline_no_cached_match",
                media_type=state.item.media_type,
                path=state.item.path,
                title=state.item.title,
            )
        helpers._record_stat(
            stats,
            "skipped",
            reason=helpers.selection_policy.no_match_skip_reason(offline=offline),
        )
        return "return", (None, False)
    if helpers._confirm("No TV candidates. Switch to movie search? [y/N]", False, progress, show_default=False):
        return "return", _reprocess_with_media_type(
            new_media_type="movie",
            item=state.item,
            library=library,
            cache=cache,
            mode=mode,
            copy_mode=copy_mode,
            interactive=interactive,
            auto_accept=auto_accept,
            min_confidence=min_confidence,
            session_tv=session_tv,
            session_wd=session_wd,
            episode_cache=episode_cache,
            progress=progress,
            show_cache=show_cache,
            stats=stats,
            incoming_root=incoming_root,
            planned=planned,
            on_conflict=on_conflict,
            allow_back=allow_back,
            offline=offline,
            allow_risky_enter_accept=allow_risky_enter_accept,
            media_type_overrides=media_type_overrides,
            tv_search_cache=tv_search_cache,
            movie_entity_cache=movie_entity_cache,
            cache_override_key=override_key,
            helpers=helpers,
            reprocess_item_fn=reprocess_item_fn,
            requested_media_type=requested_media_type,
        )
    helpers._safe_print(f"No candidates found for {helpers.rich_escape(state.item.title)}.", progress)
    choice = helpers._select_candidate(
        "tv",
        state.candidates,
        progress,
        state.has_more,
        allow_search=True,
        allow_manual=True,
        allow_back=allow_back,
        item=state.item,
    )
    if choice == "s":
        _announce_query_history(state, helpers, progress)
        item, search_query = helpers._prompt_search(state.item, progress)
        new_state = _reload_tv_loop_state(
            state=_CandidateLoopState(
                item=item,
                reference_title=state.reference_title,
                search_query=search_query,
                query_history=state.query_history,
                page=state.page,
                candidates=state.candidates,
                raw_results=state.raw_results,
                next_offset=state.next_offset,
                has_more=state.has_more,
                search_refined=True,
            ),
            session_tv=session_tv,
            cache=cache,
            show_cache=show_cache,
            incoming_root=incoming_root,
            cache_key=cache_key,
            progress=progress,
            offline=offline,
            interactive=interactive,
            tv_search_cache=tv_search_cache,
            stats=stats,
            helpers=helpers,
        )
        return ("return", (None, False)) if new_state is None else ("state", new_state)
    query = _inline_search_query(choice)
    if query:
        new_state = _reload_tv_loop_state(
            state=_CandidateLoopState(
                item=helpers._with_title(state.item, query),
                reference_title=state.reference_title,
                search_query=helpers._build_search_query(query, None),
                query_history=state.query_history,
                page=state.page,
                candidates=state.candidates,
                raw_results=state.raw_results,
                next_offset=state.next_offset,
                has_more=state.has_more,
                search_refined=True,
            ),
            session_tv=session_tv,
            cache=cache,
            show_cache=show_cache,
            incoming_root=incoming_root,
            cache_key=cache_key,
            progress=progress,
            offline=offline,
            interactive=interactive,
            tv_search_cache=tv_search_cache,
            stats=stats,
            helpers=helpers,
        )
        return ("return", (None, False)) if new_state is None else ("state", new_state)
    if choice == "m":
        return "selected", (helpers._prompt_manual_tv(state.item, progress), "manual")
    if choice == "k":
        helpers._record_stat(stats, "skipped", reason="manual_skip")
        return "return", (None, False)
    if choice == "q":
        raise helpers.typer.Exit(code=0)
    if choice == "b":
        raise helpers.BackRequested
    return "continue", None


def _handle_tv_candidate_choice(
    *,
    state: _CandidateLoopState,
    min_confidence: float,
    auto_accept: bool,
    allow_risky_enter_accept: bool,
    progress: Progress | None,
    allow_back: bool,
    session_tv: requests.Session,
    session_wd: requests.Session,
    cache: Any,
    show_cache: bool,
    incoming_root: Path | None,
    cache_key: str,
    offline: bool,
    interactive: bool,
    tv_search_cache: dict[str, list[Any]] | None,
    stats: Any,
    helpers: Any,
) -> tuple[str, Any]:
    risky_search_query = helpers.tv_matcher.broadened_search_query(
        state.reference_title,
        state.search_query,
    )
    policy = helpers._candidate_prompt_policy(
        item=state.item,
        candidates=state.candidates,
        min_confidence=min_confidence,
        cache_reusable=state.page.cache_reusable,
        allow_risky_enter_accept=allow_risky_enter_accept,
        risky_search_query=risky_search_query,
    )
    helpers._announce_candidate_prompt_policy(
        media_type="tv",
        item=state.item,
        search_query=state.search_query,
        candidates=state.candidates,
        auto_accept=auto_accept,
        allow_risky_enter_accept=allow_risky_enter_accept,
        min_confidence=min_confidence,
        cache_reusable=state.page.cache_reusable,
        policy=policy,
        progress=progress,
    )
    helpers._maybe_enrich_candidates("tv", state.candidates, session_tv, session_wd, cache, interactive)
    choice = helpers._select_candidate(
        "tv",
        state.candidates,
        progress,
        state.has_more,
        allow_search=True,
        allow_manual=True,
        allow_back=allow_back,
        item=state.item,
        allow_enter_accept=not policy.require_explicit_choice,
    )
    if isinstance(choice, helpers.Candidate):
        if policy.require_explicit_choice and choice == state.candidates[0]:
            helpers._log_explicit_risky_candidate_accept(
                media_type="tv",
                item=state.item,
                selected=choice,
                search_query=state.search_query,
            )
        return "selected", (choice, "confirmed")
    if choice == "s":
        _announce_query_history(state, helpers, progress)
        item, search_query = helpers._prompt_search(state.item, progress)
        new_state = _reload_tv_loop_state(
            state=_CandidateLoopState(
                item=item,
                reference_title=state.reference_title,
                search_query=search_query,
                query_history=state.query_history,
                page=state.page,
                candidates=state.candidates,
                raw_results=state.raw_results,
                next_offset=state.next_offset,
                has_more=state.has_more,
                search_refined=True,
            ),
            session_tv=session_tv,
            cache=cache,
            show_cache=show_cache,
            incoming_root=incoming_root,
            cache_key=cache_key,
            progress=progress,
            offline=offline,
            interactive=interactive,
            tv_search_cache=tv_search_cache,
            stats=stats,
            helpers=helpers,
        )
        return ("return", (None, False)) if new_state is None else ("state", new_state)
    query = _inline_search_query(choice)
    if query:
        new_state = _reload_tv_loop_state(
            state=_CandidateLoopState(
                item=helpers._with_title(state.item, query),
                reference_title=state.reference_title,
                search_query=helpers._build_search_query(query, None),
                query_history=state.query_history,
                page=state.page,
                candidates=state.candidates,
                raw_results=state.raw_results,
                next_offset=state.next_offset,
                has_more=state.has_more,
                search_refined=True,
            ),
            session_tv=session_tv,
            cache=cache,
            show_cache=show_cache,
            incoming_root=incoming_root,
            cache_key=cache_key,
            progress=progress,
            offline=offline,
            interactive=interactive,
            tv_search_cache=tv_search_cache,
            stats=stats,
            helpers=helpers,
        )
        return ("return", (None, False)) if new_state is None else ("state", new_state)
    if choice == "n":
        new_state = _advance_tv_loop_state(
            state=state,
            session_tv=session_tv,
            cache=cache,
            show_cache=show_cache,
            incoming_root=incoming_root,
            cache_key=cache_key,
            progress=progress,
            offline=offline,
            interactive=interactive,
            tv_search_cache=tv_search_cache,
            stats=stats,
            helpers=helpers,
        )
        return ("return", (None, False)) if new_state is None else ("state", new_state)
    if choice == "m":
        return "selected", (helpers._prompt_manual_tv(state.item, progress), "manual")
    if choice == "k":
        helpers._record_stat(stats, "skipped", reason="manual_skip")
        return "return", (None, False)
    if choice == "q":
        raise helpers.typer.Exit(code=0)
    if choice == "b":
        raise helpers.BackRequested
    return "continue", None


def _resolve_movie_manual_fallback(
    *,
    state: _CandidateLoopState,
    manual_fallback: Any | None,
    manual_hint: str,
    session_wd: requests.Session,
    cache: Any,
    show_cache: bool,
    cache_key: str,
    progress: Progress | None,
    offline: bool,
    interactive: bool,
    movie_entity_cache: dict[str, Any] | None,
    stats: Any,
    helpers: Any,
) -> tuple[str, Any, Any, str]:
    if manual_fallback is None:
        manual_fallback, manual_hint = helpers._prompt_manual_movie(state.item, progress)
    if manual_fallback.year is None and interactive:
        new_state = _reload_movie_loop_state(
            state=_CandidateLoopState(
                item=helpers._with_title(state.item, manual_fallback.title),
                reference_title=state.reference_title,
                search_query=helpers._build_search_query(manual_fallback.title, manual_hint),
                query_history=state.query_history,
                page=state.page,
                candidates=state.candidates,
                raw_results=state.raw_results,
                next_offset=state.next_offset,
                has_more=state.has_more,
                search_refined=True,
            ),
            session_wd=session_wd,
            cache=cache,
            show_cache=show_cache,
            cache_key=cache_key,
            progress=progress,
            offline=offline,
            interactive=interactive,
            movie_entity_cache=movie_entity_cache,
            stats=stats,
            helpers=helpers,
        )
        if new_state is None:
            return "selected", (manual_fallback, "manual"), manual_fallback, manual_hint
        return "state", new_state, manual_fallback, manual_hint
    return "selected", (manual_fallback, "manual"), manual_fallback, manual_hint


def _handle_movie_no_candidates(
    *,
    state: _CandidateLoopState,
    library: Path,
    cache: Any,
    mode: str,
    copy_mode: bool,
    interactive: bool,
    auto_accept: bool,
    min_confidence: float,
    session_tv: requests.Session,
    session_wd: requests.Session,
    episode_cache: Any,
    progress: Progress | None,
    show_cache: bool,
    stats: Any,
    incoming_root: Path | None,
    planned: dict[str, int] | None,
    on_conflict: str,
    allow_back: bool,
    offline: bool,
    allow_risky_enter_accept: bool,
    media_type_overrides: dict[str, str] | None,
    tv_search_cache: dict[str, list[Any]] | None,
    movie_entity_cache: dict[str, Any] | None,
    cache_key: str,
    override_key: str | None,
    helpers: Any,
    reprocess_item_fn: Any,
    manual_fallback: Any | None,
    manual_hint: str,
    requested_media_type: str | None,
) -> tuple[str, Any, Any, str]:
    if not interactive:
        if offline:
            helpers.log_event(
                helpers.logger,
                "offline_no_cached_match",
                media_type=state.item.media_type,
                path=state.item.path,
                title=state.item.title,
            )
        helpers._record_stat(
            stats,
            "skipped",
            reason=helpers.selection_policy.no_match_skip_reason(offline=offline),
        )
        return "return", (None, False), manual_fallback, manual_hint
    if requested_media_type != "movie" and helpers._confirm(
        "No movie candidates. Switch to TV search? [y/N]",
        False,
        progress,
        show_default=False,
    ):
        result = _reprocess_with_media_type(
            new_media_type="tv",
            item=state.item,
            library=library,
            cache=cache,
            mode=mode,
            copy_mode=copy_mode,
            interactive=interactive,
            auto_accept=auto_accept,
            min_confidence=min_confidence,
            session_tv=session_tv,
            session_wd=session_wd,
            episode_cache=episode_cache,
            progress=progress,
            show_cache=show_cache,
            stats=stats,
            incoming_root=incoming_root,
            planned=planned,
            on_conflict=on_conflict,
            allow_back=allow_back,
            offline=offline,
            allow_risky_enter_accept=allow_risky_enter_accept,
            media_type_overrides=media_type_overrides,
            tv_search_cache=tv_search_cache,
            movie_entity_cache=movie_entity_cache,
            cache_override_key=override_key,
            helpers=helpers,
            reprocess_item_fn=reprocess_item_fn,
            requested_media_type=requested_media_type,
        )
        return "return", result, manual_fallback, manual_hint
    helpers._safe_print(f"No candidates found for {helpers.rich_escape(state.item.title)}.", progress)
    choice = helpers._select_candidate(
        "movie",
        state.candidates,
        progress,
        state.has_more,
        allow_search=True,
        allow_manual=True,
        allow_back=allow_back,
        item=state.item,
    )
    if choice == "s":
        _announce_query_history(state, helpers, progress)
        item, search_query = helpers._prompt_search(state.item, progress)
        new_state = _reload_movie_loop_state(
            state=_CandidateLoopState(
                item=item,
                reference_title=state.reference_title,
                search_query=search_query,
                query_history=state.query_history,
                page=state.page,
                candidates=state.candidates,
                raw_results=state.raw_results,
                next_offset=state.next_offset,
                has_more=state.has_more,
                search_refined=True,
            ),
            session_wd=session_wd,
            cache=cache,
            show_cache=show_cache,
            cache_key=cache_key,
            progress=progress,
            offline=offline,
            interactive=interactive,
            movie_entity_cache=movie_entity_cache,
            stats=stats,
            helpers=helpers,
        )
        if new_state is None:
            return "return", (None, False), manual_fallback, manual_hint
        return "state", new_state, manual_fallback, manual_hint
    query = _inline_search_query(choice)
    if query:
        new_state = _reload_movie_loop_state(
            state=_CandidateLoopState(
                item=helpers._with_title(state.item, query),
                reference_title=state.reference_title,
                search_query=helpers._build_search_query(query, None),
                query_history=state.query_history,
                page=state.page,
                candidates=state.candidates,
                raw_results=state.raw_results,
                next_offset=state.next_offset,
                has_more=state.has_more,
                search_refined=True,
            ),
            session_wd=session_wd,
            cache=cache,
            show_cache=show_cache,
            cache_key=cache_key,
            progress=progress,
            offline=offline,
            interactive=interactive,
            movie_entity_cache=movie_entity_cache,
            stats=stats,
            helpers=helpers,
        )
        if new_state is None:
            return "return", (None, False), manual_fallback, manual_hint
        return "state", new_state, manual_fallback, manual_hint
    if choice == "m":
        return _resolve_movie_manual_fallback(
            state=state,
            manual_fallback=manual_fallback,
            manual_hint=manual_hint,
            session_wd=session_wd,
            cache=cache,
            show_cache=show_cache,
            cache_key=cache_key,
            progress=progress,
            offline=offline,
            interactive=interactive,
            movie_entity_cache=movie_entity_cache,
            stats=stats,
            helpers=helpers,
        )
    if choice == "k":
        helpers._record_stat(stats, "skipped", reason="manual_skip")
        return "return", (None, False), manual_fallback, manual_hint
    if choice == "q":
        raise helpers.typer.Exit(code=0)
    if choice == "b":
        raise helpers.BackRequested
    return "continue", None, manual_fallback, manual_hint


def _handle_movie_candidate_choice(
    *,
    state: _CandidateLoopState,
    min_confidence: float,
    auto_accept: bool,
    allow_risky_enter_accept: bool,
    progress: Progress | None,
    allow_back: bool,
    session_tv: requests.Session,
    session_wd: requests.Session,
    cache: Any,
    show_cache: bool,
    cache_key: str,
    offline: bool,
    interactive: bool,
    movie_entity_cache: dict[str, Any] | None,
    stats: Any,
    helpers: Any,
    manual_fallback: Any | None,
    manual_hint: str,
) -> tuple[str, Any, Any, str]:
    risky_search_query = state.search_refined and helpers.movie_matcher.broadened_search_query(
        state.reference_title,
        state.search_query,
    )
    policy = helpers._candidate_prompt_policy(
        item=state.item,
        candidates=state.candidates,
        min_confidence=min_confidence,
        cache_reusable=state.page.cache_reusable,
        allow_risky_enter_accept=allow_risky_enter_accept,
        risky_search_query=risky_search_query,
    )
    helpers._announce_candidate_prompt_policy(
        media_type="movie",
        item=state.item,
        search_query=state.search_query,
        candidates=state.candidates,
        auto_accept=auto_accept,
        allow_risky_enter_accept=allow_risky_enter_accept,
        min_confidence=min_confidence,
        cache_reusable=state.page.cache_reusable,
        policy=policy,
        progress=progress,
    )
    helpers._maybe_enrich_candidates("movie", state.candidates, session_tv, session_wd, cache, interactive)
    choice = helpers._select_candidate(
        "movie",
        state.candidates,
        progress,
        state.has_more,
        allow_search=True,
        allow_manual=True,
        allow_back=allow_back,
        item=state.item,
        allow_enter_accept=not policy.require_explicit_choice,
    )
    if isinstance(choice, helpers.Candidate):
        if policy.require_explicit_choice and choice == state.candidates[0]:
            helpers._log_explicit_risky_candidate_accept(
                media_type="movie",
                item=state.item,
                selected=choice,
                search_query=state.search_query,
            )
        return "selected", (choice, "confirmed"), manual_fallback, manual_hint
    if choice == "s":
        _announce_query_history(state, helpers, progress)
        item, search_query = helpers._prompt_search(state.item, progress)
        new_state = _reload_movie_loop_state(
            state=_CandidateLoopState(
                item=item,
                reference_title=state.reference_title,
                search_query=search_query,
                query_history=state.query_history,
                page=state.page,
                candidates=state.candidates,
                raw_results=state.raw_results,
                next_offset=state.next_offset,
                has_more=state.has_more,
                search_refined=True,
            ),
            session_wd=session_wd,
            cache=cache,
            show_cache=show_cache,
            cache_key=cache_key,
            progress=progress,
            offline=offline,
            interactive=interactive,
            movie_entity_cache=movie_entity_cache,
            stats=stats,
            helpers=helpers,
        )
        if new_state is None:
            return "return", (None, False), manual_fallback, manual_hint
        return "state", new_state, manual_fallback, manual_hint
    query = _inline_search_query(choice)
    if query:
        new_state = _reload_movie_loop_state(
            state=_CandidateLoopState(
                item=helpers._with_title(state.item, query),
                reference_title=state.reference_title,
                search_query=helpers._build_search_query(query, None),
                query_history=state.query_history,
                page=state.page,
                candidates=state.candidates,
                raw_results=state.raw_results,
                next_offset=state.next_offset,
                has_more=state.has_more,
                search_refined=True,
            ),
            session_wd=session_wd,
            cache=cache,
            show_cache=show_cache,
            cache_key=cache_key,
            progress=progress,
            offline=offline,
            interactive=interactive,
            movie_entity_cache=movie_entity_cache,
            stats=stats,
            helpers=helpers,
        )
        if new_state is None:
            return "return", (None, False), manual_fallback, manual_hint
        return "state", new_state, manual_fallback, manual_hint
    if choice == "n":
        new_state = _advance_movie_loop_state(
            state=state,
            session_wd=session_wd,
            cache=cache,
            show_cache=show_cache,
            cache_key=cache_key,
            progress=progress,
            offline=offline,
            interactive=interactive,
            movie_entity_cache=movie_entity_cache,
            stats=stats,
            helpers=helpers,
        )
        if new_state is None:
            return "return", (None, False), manual_fallback, manual_hint
        return "state", new_state, manual_fallback, manual_hint
    if choice == "m":
        return _resolve_movie_manual_fallback(
            state=state,
            manual_fallback=manual_fallback,
            manual_hint=manual_hint,
            session_wd=session_wd,
            cache=cache,
            show_cache=show_cache,
            cache_key=cache_key,
            progress=progress,
            offline=offline,
            interactive=interactive,
            movie_entity_cache=movie_entity_cache,
            stats=stats,
            helpers=helpers,
        )
    if choice == "k":
        helpers._record_stat(stats, "skipped", reason="manual_skip")
        return "return", (None, False), manual_fallback, manual_hint
    if choice == "q":
        raise helpers.typer.Exit(code=0)
    if choice == "b":
        raise helpers.BackRequested
    return "continue", None, manual_fallback, manual_hint


def _finalize_tv_selection(
    *,
    item: Any,
    reference_title: str,
    search_refined: bool,
    selected: Any,
    outcome: str | None,
    candidates: list[Any],
    search_query: str,
    fallback_attempts: int,
    library: Path,
    cache: Any,
    cache_key: str,
    folder_show_key: str | None,
    reusable_show_key: str | None,
    reusable_episode_key: str | None,
    mode: str,
    on_conflict: str,
    planned: dict[str, int] | None,
    progress: Progress | None,
    stats: Any,
    offline: bool,
    interactive: bool,
    session_tv: requests.Session,
    episode_cache: Any,
    helpers: Any,
) -> tuple[Any | None, bool]:
    if not selected:
        helpers._record_stat(stats, "skipped")
        return None, False
    if selected.metadata.get("manual"):
        outcome = "manual"
    if outcome is None:
        outcome = "confirmed"
    helpers._record_stat(stats, outcome)
    helpers._print_choice(selected, progress)
    helpers.log_event(
        helpers.logger,
        "candidate_selected",
        media_type="tv",
        selection_mode=outcome,
        selection_source=selected.source,
        decision_reason="user_or_auto_selection",
        path=item.path,
        title=selected.title,
        year=selected.year,
        query=search_query,
        confidence=selected.confidence,
        cache_scope="tv",
    )
    helpers._maybe_fetch_episode_title(item, selected, session_tv, episode_cache, bump_confidence=False)
    metadata = selected.metadata
    confirmed_by_user = outcome in {"confirmed", "manual"}
    promote_reusable = helpers._should_promote_to_reusable(selection_mode=outcome, selected=selected, candidates=candidates)
    risky_search_query = helpers.tv_matcher.broadened_search_query(reference_title, search_query)
    season = metadata.get("season") or item.season
    episode = metadata.get("episode") or item.episode
    episode_end = metadata.get("episode_end") or item.episode_end
    episode_title = metadata.get("episode_title") or item.episode_title
    if episode is not None and int(episode) > helpers.MAX_PLAUSIBLE_EPISODE_NUMBER:
        auto_resolved = helpers._auto_resolve_episode_from_title(item, metadata.get("id"), session_tv, episode_cache)
        if auto_resolved is not None:
            season, episode, resolved_title = auto_resolved
            episode_title = resolved_title or episode_title
            metadata["episode_title"] = episode_title
        else:
            helpers.log_event(
                helpers.logger,
                "implausible_episode_number",
                level=30,
                path=item.path,
                title=item.title,
                season=season,
                episode=episode,
            )
    if interactive and (season is None or episode is None) and item.episode_title:
        resolved = helpers._resolve_episode_from_title(item, metadata.get("id"), session_tv, episode_cache, progress)
        if resolved:
            season, episode, resolved_title = resolved
            episode_title = resolved_title or episode_title
            metadata["episode_title"] = episode_title
    if season is None or episode is None:
        if not interactive:
            return None, False
        season_prompt = helpers._prompt_int_or_control("Season", item.season or 1, progress)
        if season_prompt == "k":
            helpers._record_stat(
                stats,
                "skipped",
                reason=helpers.selection_policy.no_match_skip_reason(offline=offline),
            )
            return None, False
        if season_prompt == "q":
            raise helpers.typer.Exit(code=0)
        season = season_prompt
        episode_prompt = helpers._prompt_int_or_control("Episode", item.episode or 1, progress)
        if episode_prompt == "k":
            helpers._record_stat(stats, "skipped")
            return None, False
        if episode_prompt == "q":
            raise helpers.typer.Exit(code=0)
        episode = episode_prompt
        if not episode_title:
            episode_title = helpers._prompt_text("Episode title (optional)", item.episode_title or "", progress)

    metadata["episode_title"] = episode_title
    if selected.metadata.get("manual"):
        entry = {
            "id": None,
            "name": metadata["name"],
            "premiered": None,
            "chosen_title": metadata["name"],
            "chosen_year": metadata.get("year"),
            "season": season,
            "episode": episode,
            "episode_end": episode_end,
            "episode_title": episode_title,
            "manual": True,
            "confirmed_by_user": confirmed_by_user,
            "selection_mode": outcome,
            "created_at": helpers.now_timestamp(),
            "source": "Manual",
            "search_query": search_query,
            "search_refined": search_refined,
            "risky_search_query": risky_search_query,
            "fallback_attempts": fallback_attempts,
        }
        show_entry = {
            "id": None,
            "name": metadata["name"],
            "premiered": None,
            "chosen_title": metadata["name"],
            "chosen_year": metadata.get("year"),
            "season": season,
            "manual": True,
            "confirmed_by_user": confirmed_by_user,
            "selection_mode": outcome,
            "created_at": helpers.now_timestamp(),
            "source": "Manual",
            "search_query": search_query,
            "search_refined": search_refined,
            "risky_search_query": risky_search_query,
            "fallback_attempts": fallback_attempts,
        }
    else:
        entry = {
            "id": metadata["id"],
            "name": selected.title,
            "premiered": selected.year,
            "chosen_title": selected.title,
            "chosen_year": selected.year,
            "season": season,
            "episode": episode,
            "episode_end": episode_end,
            "episode_title": episode_title,
            "manual": False,
            "confirmed_by_user": confirmed_by_user,
            "selection_mode": outcome,
            "created_at": helpers.now_timestamp(),
            "source": selected.source,
            "search_query": search_query,
            "search_refined": search_refined,
            "risky_search_query": risky_search_query,
            "fallback_attempts": fallback_attempts,
        }
        show_entry = {
            "id": metadata["id"],
            "name": selected.title,
            "premiered": selected.year,
            "chosen_title": selected.title,
            "chosen_year": selected.year,
            "manual": False,
            "confirmed_by_user": confirmed_by_user,
            "selection_mode": outcome,
            "created_at": helpers.now_timestamp(),
            "source": selected.source,
            "search_query": search_query,
            "search_refined": search_refined,
            "risky_search_query": risky_search_query,
            "fallback_attempts": fallback_attempts,
        }
    cache_write_level = "trusted"
    cache.set_show(cache_key, entry)
    if risky_search_query:
        cache_write_level = "file_only"
        helpers._safe_print(
            "Trusted TV cache promotion suppressed because the effective search query broadened the title.",
            progress,
        )
        helpers.log_event(
            helpers.logger,
            "cache_write_suppressed_risky_search",
            media_type="tv",
            path=item.path,
            title=reference_title,
            query=search_query,
            cache_scope="tv",
            selection_mode=outcome,
        )
    if reusable_show_key and promote_reusable and not risky_search_query:
        if helpers._reusable_tv_cache_safe(item):
            helpers._promote_reusable_with_conflict_tracking("tv", cache=cache, key=reusable_show_key, entry=show_entry)
        else:
            helpers.log_event(
                helpers.logger,
                "reusable_cache_blocked_ambiguous_title",
                media_type="tv",
                title=item.title,
                year=item.year,
                key=reusable_show_key,
            )
    if folder_show_key and not risky_search_query and helpers.selection_policy.should_write_folder_show_cache(
        confirmed_by_user=confirmed_by_user,
        selection_mode=outcome,
        manual=bool(selected.metadata.get("manual")),
    ):
        folder_entry = dict(show_entry)
        if season is not None:
            folder_entry["season"] = season
        cache.set_show(folder_show_key, folder_entry)
    if reusable_episode_key and promote_reusable and not risky_search_query:
        if helpers._reusable_tv_cache_safe(item):
            cache.set_show(reusable_episode_key, entry)
        else:
            helpers.log_event(
                helpers.logger,
                "reusable_cache_blocked_ambiguous_title",
                media_type="tv",
                title=item.title,
                year=item.year,
                key=reusable_episode_key,
            )
    helpers._save_cache(cache, progress)
    destination = helpers.plan_tv_show(
        library,
        metadata.get("name") or selected.title,
        metadata.get("year") or selected.year,
        int(season),
        int(episode),
        int(episode_end) if episode_end is not None else None,
        metadata.get("episode_title") or episode_title,
        item.path.suffix,
    )
    destination, collision = helpers._resolve_destination(destination, on_conflict, planned, progress)
    if destination is None:
        helpers._record_stat(stats, "skipped", reason="conflict_skip")
        return None, False
    if len(str(destination)) > 240:
        helpers._safe_print("Warning: destination path is very long and may exceed Windows limits.", progress)
    plan = helpers.MovePlan(
        source=item.path,
        destination=destination,
        mode=mode,
        media_type="tv",
        metadata={
            "show": metadata.get("name") or selected.title,
            "year": metadata.get("year") or selected.year,
            "season": int(season),
            "episode": int(episode),
            "episode_end": int(episode_end) if episode_end is not None else None,
            "episode_title": metadata.get("episode_title") or episode_title,
            "selection": {
                "source": selected.source,
                "mode": outcome,
                "reference_title": reference_title,
                "effective_query": search_query,
                "search_refined": search_refined,
                "risky_search_query": risky_search_query,
                "fallback_attempts": fallback_attempts,
                "cache_write_level": cache_write_level,
            },
        },
    )
    helpers._print_plan(plan, progress)
    helpers.log_event(
        helpers.logger,
        "plan_created",
        source_path=item.path,
        destination=destination,
        media_type="tv",
        title=metadata.get("name") or selected.title,
        year=metadata.get("year") or selected.year,
        season=int(season),
        episode=int(episode),
        episode_end=int(episode_end) if episode_end is not None else None,
    )
    return plan, collision


def _finalize_movie_selection(
    *,
    item: Any,
    reference_title: str,
    search_refined: bool,
    selected: Any,
    outcome: str | None,
    candidates: list[Any],
    search_query: str,
    fallback_attempts: int,
    library: Path,
    cache: Any,
    cache_key: str,
    reusable_movie_key: str | None,
    mode: str,
    on_conflict: str,
    planned: dict[str, int] | None,
    progress: Progress | None,
    stats: Any,
    helpers: Any,
    interactive: bool,
) -> tuple[Any | None, bool]:
    if not selected:
        helpers._record_stat(stats, "skipped")
        return None, False
    if selected.metadata.get("manual"):
        outcome = "manual"
    if outcome is None:
        outcome = "confirmed"
    helpers._record_stat(stats, outcome)
    helpers._print_choice(selected, progress)
    helpers.log_event(
        helpers.logger,
        "candidate_selected",
        media_type="movie",
        selection_mode=outcome,
        selection_source=selected.source,
        decision_reason="user_or_auto_selection",
        path=item.path,
        title=selected.title,
        year=selected.year,
        query=search_query,
        confidence=selected.confidence,
        cache_scope="movie",
    )
    metadata = selected.metadata
    confirmed_by_user = outcome in {"confirmed", "manual"}
    promote_reusable = helpers._should_promote_to_reusable(selection_mode=outcome, selected=selected, candidates=candidates)
    risky_search_query = helpers.movie_matcher.broadened_search_query(reference_title, search_query)
    if metadata.get("manual"):
        entry = {
            "qid": None,
            "title": metadata["title"],
            "year": metadata.get("year"),
            "chosen_title": metadata["title"],
            "chosen_year": metadata.get("year"),
            "manual": True,
            "confirmed_by_user": confirmed_by_user,
            "selection_mode": outcome,
            "created_at": helpers.now_timestamp(),
            "source": "Manual",
            "search_query": search_query,
            "search_refined": search_refined,
            "risky_search_query": risky_search_query,
            "fallback_attempts": fallback_attempts,
        }
    else:
        entry = {
            "qid": metadata["qid"],
            "title": selected.title,
            "year": selected.year,
            "chosen_title": selected.title,
            "chosen_year": selected.year,
            "manual": False,
            "confirmed_by_user": confirmed_by_user,
            "selection_mode": outcome,
            "created_at": helpers.now_timestamp(),
            "source": selected.source,
            "search_query": search_query,
            "search_refined": search_refined,
            "risky_search_query": risky_search_query,
            "fallback_attempts": fallback_attempts,
        }
    cache.set_movie(cache_key, entry)
    if reusable_movie_key and promote_reusable:
        if helpers._reusable_movie_cache_safe(item):
            helpers._promote_reusable_with_conflict_tracking("movie", cache=cache, key=reusable_movie_key, entry=entry)
        else:
            helpers.log_event(
                helpers.logger,
                "reusable_cache_blocked_ambiguous_title",
                media_type="movie",
                title=item.title,
                year=item.year,
                key=reusable_movie_key,
            )
    helpers._save_cache(cache, progress)

    year = metadata.get("year") or selected.year
    if year is None and interactive:
        year_text = helpers._prompt_text("Movie year (optional, helps disambiguate)", "", progress, show_default=False)
        year = int(year_text) if year_text else None
    destination = helpers.plan_movie(library, metadata.get("title") or selected.title, year, item.path.suffix)
    destination, collision = helpers._resolve_destination(destination, on_conflict, planned, progress)
    if destination is None:
        helpers._record_stat(stats, "skipped", reason="conflict_skip")
        return None, False
    if len(str(destination)) > 240:
        helpers._safe_print("Warning: destination path is very long and may exceed Windows limits.", progress)
    plan = helpers.MovePlan(
        source=item.path,
        destination=destination,
        mode=mode,
        media_type="movie",
        metadata={
            "title": metadata.get("title") or selected.title,
            "year": year,
            "selection": {
                "source": selected.source,
                "mode": outcome,
                "reference_title": reference_title,
                "effective_query": search_query,
                "search_refined": search_refined,
                "risky_search_query": risky_search_query,
                "fallback_attempts": fallback_attempts,
            },
        },
    )
    helpers._print_plan(plan, progress)
    helpers.log_event(
        helpers.logger,
        "plan_created",
        source_path=item.path,
        destination=destination,
        media_type="movie",
        title=metadata.get("title") or selected.title,
        year=year,
    )
    return plan, collision


def process_video_item(
    item: Any,
    library: Path,
    cache: Any,
    mode: str,
    copy_mode: bool,
    interactive: bool,
    auto_accept: bool,
    min_confidence: float,
    session_tv: requests.Session,
    session_wd: requests.Session,
    episode_cache: Any,
    progress: Progress | None,
    show_cache: bool,
    stats: Any = None,
    incoming_root: Path | None = None,
    planned: dict[str, int] | None = None,
    on_conflict: str = "rename",
    allow_back: bool = False,
    offline: bool = False,
    allow_risky_enter_accept: bool = False,
    media_type_overrides: dict[str, str] | None = None,
    tv_search_cache: dict[str, list[Any]] | None = None,
    movie_entity_cache: dict[str, Any] | None = None,
    requested_media_type: str | None = None,
    helpers: Any = None,
    reprocess_item_fn: Any = None,
) -> tuple[Any | None, bool]:
    if item.media_type == "tv":
        return process_tv_item(
            item=item,
            library=library,
            cache=cache,
            mode=mode,
            copy_mode=copy_mode,
            interactive=interactive,
            auto_accept=auto_accept,
            min_confidence=min_confidence,
            session_tv=session_tv,
            session_wd=session_wd,
            episode_cache=episode_cache,
            progress=progress,
            show_cache=show_cache,
            stats=stats,
            incoming_root=incoming_root,
            planned=planned,
            on_conflict=on_conflict,
            allow_back=allow_back,
            offline=offline,
            allow_risky_enter_accept=allow_risky_enter_accept,
            media_type_overrides=media_type_overrides,
            tv_search_cache=tv_search_cache,
            movie_entity_cache=movie_entity_cache,
            requested_media_type=requested_media_type,
            helpers=helpers,
            reprocess_item_fn=reprocess_item_fn,
        )
    return process_movie_item(
        item=item,
        library=library,
        cache=cache,
        mode=mode,
        copy_mode=copy_mode,
        interactive=interactive,
        auto_accept=auto_accept,
        min_confidence=min_confidence,
        session_tv=session_tv,
        session_wd=session_wd,
        episode_cache=episode_cache,
        progress=progress,
        show_cache=show_cache,
        stats=stats,
        incoming_root=incoming_root,
        planned=planned,
        on_conflict=on_conflict,
        allow_back=allow_back,
        offline=offline,
        allow_risky_enter_accept=allow_risky_enter_accept,
        media_type_overrides=media_type_overrides,
        tv_search_cache=tv_search_cache,
        movie_entity_cache=movie_entity_cache,
        requested_media_type=requested_media_type,
        helpers=helpers,
        reprocess_item_fn=reprocess_item_fn,
    )


def process_tv_item(
    item: Any,
    library: Path,
    cache: Any,
    mode: str,
    copy_mode: bool,
    interactive: bool,
    auto_accept: bool,
    min_confidence: float,
    session_tv: requests.Session,
    session_wd: requests.Session,
    episode_cache: Any,
    progress: Progress | None,
    show_cache: bool,
    stats: Any = None,
    incoming_root: Path | None = None,
    planned: dict[str, int] | None = None,
    on_conflict: str = "rename",
    allow_back: bool = False,
    offline: bool = False,
    allow_risky_enter_accept: bool = False,
    media_type_overrides: dict[str, str] | None = None,
    tv_search_cache: dict[str, list[Any]] | None = None,
    movie_entity_cache: dict[str, Any] | None = None,
    requested_media_type: str | None = None,
    helpers: Any = None,
    reprocess_item_fn: Any = None,
) -> tuple[Any | None, bool]:
    item, override_key, folder_show_key = _prepare_item_context(
        item=item,
        cache=cache,
        incoming_root=incoming_root,
        media_type_overrides=media_type_overrides,
        helpers=helpers,
    )
    cache_key = helpers.build_cache_key(item.path, incoming_root, item.media_type, item.year)
    if item.media_type == "movie" and interactive:
        looks_like_tv = (
            item.season is not None
            or item.episode is not None
            or helpers.TV_EXPLICIT_SEASON_RE.search(item.path.stem) is not None
            or helpers.TV_EXPLICIT_SEASON_EPISODE_RE.search(item.path.stem) is not None
        )
        if looks_like_tv:
            if helpers._confirm("This looks like TV. Treat as TV? [Y/n]", True, progress, show_default=False):
                item = helpers._switch_item_media_type(item, "tv")
                helpers._persist_media_type_override(cache, override_key, "tv", media_type_overrides, progress)
    reusable_movie_key = None
    reusable_show_key = None
    reusable_episode_key = None
    if item.media_type == "tv":
        reusable_safe = helpers._reusable_tv_cache_safe(item)
        if reusable_safe:
            reusable_show_key = helpers.tv_show_cache_key(item.title, item.year)
        if reusable_safe and item.season is not None and item.episode is not None:
            reusable_episode_key = helpers.tv_episode_cache_key(item.title, item.year, item.season, item.episode)
    else:
        reusable_movie_key = helpers.movie_cache_key(item.title, item.year)
    collision = False
    if item.media_type == "tv":
        raw_results_tv: list[Any] | None = None
        next_offset = 0
        search_query = helpers._build_search_query(item.title, None)
        page = _load_tv_candidate_page(
            item=item,
            session_tv=session_tv,
            cache=cache,
            show_cache=show_cache,
            incoming_root=incoming_root,
            cache_key=cache_key,
            next_offset=next_offset,
            raw_results_tv=raw_results_tv,
            search_query=search_query,
            progress=progress,
            offline=offline,
            interactive=interactive,
            tv_search_cache=tv_search_cache,
            helpers=helpers,
        )
        if page is None:
            return None, False
        candidates, raw_results_tv, next_offset, has_more = _apply_candidate_page(page, stats, helpers)
        state = _CandidateLoopState(
            item=item,
            reference_title=item.title,
            search_query=page.search_query_used or search_query,
            query_history=_merge_query_history(page.attempted_queries),
            page=page,
            candidates=candidates,
            raw_results=raw_results_tv,
            next_offset=next_offset,
            has_more=has_more,
            search_refined=False,
        )
        selected = None
        outcome = None
        while True:
            if not state.candidates:
                status, payload = _handle_tv_no_candidates(
                    state=state,
                    library=library,
                    cache=cache,
                    mode=mode,
                    copy_mode=copy_mode,
                    interactive=interactive,
                    auto_accept=auto_accept,
                    min_confidence=min_confidence,
                    session_tv=session_tv,
                    session_wd=session_wd,
                    episode_cache=episode_cache,
                    progress=progress,
                    show_cache=show_cache,
                    stats=stats,
                    incoming_root=incoming_root,
                    planned=planned,
                    on_conflict=on_conflict,
                    allow_back=allow_back,
                    offline=offline,
                    allow_risky_enter_accept=allow_risky_enter_accept,
                    media_type_overrides=media_type_overrides,
                    tv_search_cache=tv_search_cache,
                    movie_entity_cache=movie_entity_cache,
                    cache_key=cache_key,
                    override_key=override_key,
                    requested_media_type=requested_media_type,
                    helpers=helpers,
                    reprocess_item_fn=reprocess_item_fn,
                )
                if status == "return":
                    return payload
                if status == "state":
                    state = payload
                    continue
                if status == "selected":
                    selected, outcome = payload
                    break
                continue
            helpers._maybe_fetch_episode_title(state.item, state.candidates[0], session_tv, episode_cache, bump_confidence=True)
            risky_search_query = helpers.tv_matcher.broadened_search_query(state.reference_title, state.search_query)
            if not state.search_refined and not risky_search_query:
                selected = helpers._maybe_auto_select_candidate(
                    candidates=state.candidates,
                    auto_accept=auto_accept,
                    min_confidence=min_confidence,
                    title=state.reference_title,
                    search_query=state.search_query,
                    target_year=state.item.year,
                    progress=progress,
                )
                if selected is not None:
                    outcome = "auto"
                    break
            if not interactive:
                helpers._record_stat(
                    stats,
                    "skipped",
                    reason=helpers.selection_policy.no_match_skip_reason(offline=offline),
                )
                return None, False
            status, payload = _handle_tv_candidate_choice(
                state=state,
                min_confidence=min_confidence,
                auto_accept=auto_accept,
                allow_risky_enter_accept=allow_risky_enter_accept,
                progress=progress,
                allow_back=allow_back,
                session_tv=session_tv,
                session_wd=session_wd,
                cache=cache,
                show_cache=show_cache,
                incoming_root=incoming_root,
                cache_key=cache_key,
                offline=offline,
                interactive=interactive,
                tv_search_cache=tv_search_cache,
                stats=stats,
                helpers=helpers,
            )
            if status == "return":
                return payload
            if status == "state":
                state = payload
                continue
            if status == "selected":
                selected, outcome = payload
                break
        return _finalize_tv_selection(
            item=state.item,
            reference_title=state.reference_title,
            search_refined=state.search_refined,
            selected=selected,
            outcome=outcome,
            candidates=state.candidates,
            search_query=state.search_query,
            fallback_attempts=state.page.fallback_attempts,
            library=library,
            cache=cache,
            cache_key=cache_key,
            folder_show_key=folder_show_key,
            reusable_show_key=reusable_show_key,
            reusable_episode_key=reusable_episode_key,
            mode=mode,
            on_conflict=on_conflict,
            planned=planned,
            progress=progress,
            stats=stats,
            offline=offline,
            interactive=interactive,
            session_tv=session_tv,
            episode_cache=episode_cache,
            helpers=helpers,
        )

def process_movie_item(
    item: Any,
    library: Path,
    cache: Any,
    mode: str,
    copy_mode: bool,
    interactive: bool,
    auto_accept: bool,
    min_confidence: float,
    session_tv: requests.Session,
    session_wd: requests.Session,
    episode_cache: Any,
    progress: Progress | None,
    show_cache: bool,
    stats: Any = None,
    incoming_root: Path | None = None,
    planned: dict[str, int] | None = None,
    on_conflict: str = "rename",
    allow_back: bool = False,
    offline: bool = False,
    allow_risky_enter_accept: bool = False,
    media_type_overrides: dict[str, str] | None = None,
    tv_search_cache: dict[str, list[Any]] | None = None,
    movie_entity_cache: dict[str, Any] | None = None,
    requested_media_type: str | None = None,
    helpers: Any = None,
    reprocess_item_fn: Any = None,
) -> tuple[Any | None, bool]:
    item, override_key, folder_show_key = _prepare_item_context(
        item=item,
        cache=cache,
        incoming_root=incoming_root,
        media_type_overrides=media_type_overrides,
        helpers=helpers,
    )
    cache_key = helpers.build_cache_key(item.path, incoming_root, item.media_type, item.year)
    if item.media_type == "tv":
        return process_tv_item(
            item=item,
            library=library,
            cache=cache,
            mode=mode,
            copy_mode=copy_mode,
            interactive=interactive,
            auto_accept=auto_accept,
            min_confidence=min_confidence,
            session_tv=session_tv,
            session_wd=session_wd,
            episode_cache=episode_cache,
            progress=progress,
            show_cache=show_cache,
            stats=stats,
            incoming_root=incoming_root,
            planned=planned,
            on_conflict=on_conflict,
            allow_back=allow_back,
            offline=offline,
            allow_risky_enter_accept=allow_risky_enter_accept,
            media_type_overrides=media_type_overrides,
            tv_search_cache=tv_search_cache,
            movie_entity_cache=movie_entity_cache,
            requested_media_type=requested_media_type,
            helpers=helpers,
            reprocess_item_fn=reprocess_item_fn,
        )
    if item.media_type == "movie" and interactive:
        looks_like_tv = (
            item.season is not None
            or item.episode is not None
            or helpers.TV_EXPLICIT_SEASON_RE.search(item.path.stem) is not None
            or helpers.TV_EXPLICIT_SEASON_EPISODE_RE.search(item.path.stem) is not None
        )
        if looks_like_tv:
            if helpers._confirm("This looks like TV. Treat as TV? [Y/n]", True, progress, show_default=False):
                item = helpers._switch_item_media_type(item, "tv")
                helpers._persist_media_type_override(cache, override_key, "tv", media_type_overrides, progress)
    reusable_movie_key = helpers.movie_cache_key(item.title, item.year)

    raw_results_movie: list[Any] | None = None
    next_offset = 0
    movie_page_limit = 1 if auto_accept and not interactive else 5
    search_query = helpers._build_search_query(item.title, None)
    page = _load_movie_candidate_page(
        item=item,
        session_wd=session_wd,
        cache=cache,
        show_cache=show_cache,
        cache_key=cache_key,
        next_offset=next_offset,
        raw_results_movie=raw_results_movie,
        search_query=search_query,
        progress=progress,
        limit=movie_page_limit,
        offline=offline,
        interactive=interactive,
        movie_entity_cache=movie_entity_cache,
        helpers=helpers,
    )
    if page is None:
        return None, False
    candidates, raw_results_movie, next_offset, has_more = _apply_candidate_page(page, stats, helpers)
    state = _CandidateLoopState(
        item=item,
        reference_title=item.title,
        search_query=page.search_query_used or search_query,
        query_history=_merge_query_history(page.attempted_queries),
        page=page,
        candidates=candidates,
        raw_results=raw_results_movie,
        next_offset=next_offset,
        has_more=has_more,
        search_refined=False,
    )
    selected = None
    manual_fallback: Any | None = None
    manual_hint = ""
    outcome = None
    while True:
        if not state.candidates:
            status, payload, manual_fallback, manual_hint = _handle_movie_no_candidates(
                state=state,
                library=library,
                cache=cache,
                mode=mode,
                copy_mode=copy_mode,
                interactive=interactive,
                auto_accept=auto_accept,
                min_confidence=min_confidence,
                session_tv=session_tv,
                session_wd=session_wd,
                episode_cache=episode_cache,
                progress=progress,
                show_cache=show_cache,
                stats=stats,
                incoming_root=incoming_root,
                planned=planned,
                on_conflict=on_conflict,
                allow_back=allow_back,
                offline=offline,
                allow_risky_enter_accept=allow_risky_enter_accept,
                media_type_overrides=media_type_overrides,
                tv_search_cache=tv_search_cache,
                movie_entity_cache=movie_entity_cache,
                cache_key=cache_key,
                override_key=override_key,
                helpers=helpers,
                reprocess_item_fn=reprocess_item_fn,
                manual_fallback=manual_fallback,
                manual_hint=manual_hint,
                requested_media_type=requested_media_type,
            )
            if status == "return":
                return payload
            if status == "state":
                state = payload
                continue
            if status == "selected":
                selected, outcome = payload
                break
            continue
        if not state.search_refined:
            selected = helpers._maybe_auto_select_candidate(
                candidates=state.candidates,
                auto_accept=auto_accept,
                min_confidence=min_confidence,
                title=state.reference_title,
                search_query=state.search_query,
                target_year=state.item.year,
                progress=progress,
            )
            if selected is not None:
                outcome = "auto"
                break
        if not interactive:
            helpers._record_stat(stats, "skipped")
            return None, False
        status, payload, manual_fallback, manual_hint = _handle_movie_candidate_choice(
            state=state,
            min_confidence=min_confidence,
            auto_accept=auto_accept,
            allow_risky_enter_accept=allow_risky_enter_accept,
            progress=progress,
            allow_back=allow_back,
            session_tv=session_tv,
            session_wd=session_wd,
            cache=cache,
            show_cache=show_cache,
            cache_key=cache_key,
            offline=offline,
            interactive=interactive,
            movie_entity_cache=movie_entity_cache,
            stats=stats,
            helpers=helpers,
            manual_fallback=manual_fallback,
            manual_hint=manual_hint,
        )
        if status == "return":
            return payload
        if status == "state":
            state = payload
            continue
        if status == "selected":
            selected, outcome = payload
            break
    return _finalize_movie_selection(
        item=state.item,
        reference_title=state.reference_title,
        search_refined=state.search_refined,
        selected=selected,
        outcome=outcome,
        candidates=state.candidates,
        search_query=state.search_query,
        fallback_attempts=state.page.fallback_attempts,
        library=library,
        cache=cache,
        cache_key=cache_key,
        reusable_movie_key=reusable_movie_key,
        mode=mode,
        on_conflict=on_conflict,
        planned=planned,
        progress=progress,
        stats=stats,
        helpers=helpers,
        interactive=interactive,
    )




