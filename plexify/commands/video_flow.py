from __future__ import annotations

import sys
import time
import re
from pathlib import Path
from typing import Any

import requests

from ..cache import Cache, NullCache
from ..infer import InferredItem
from ..sources import tvmaze, wikidata
from ..tv_episode_cache import EpisodeCache
from ..util import build_cache_key, iter_video_files, movie_cache_key, tv_episode_cache_key, tv_show_cache_key, tv_show_folder_cache_key
from ..cache_policy import is_ambiguous_cache_title


WIKIDATA_DESCRIPTION_YEAR_RE = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")


def reusable_cache_hit_looks_risky(item: InferredItem, top_confidence: float, min_confidence: float) -> bool:
    if is_ambiguous_cache_title(item.title):
        return True
    if item.year is None:
        return True
    return top_confidence < min_confidence


def preview_group_key(plan: Any) -> str:
    if plan.media_type == "tv":
        show = str(plan.metadata.get("show") or "").strip().casefold()
        if show:
            return f"tv:{show}"
    if plan.media_type == "movie":
        title = str(plan.metadata.get("title") or "").strip().casefold()
        if title:
            return f"movie:{title}"
    return f"path:{str(plan.destination.parent).casefold()}"


def select_preview_plans(plans: list[Any], limit: int = 5) -> list[Any]:
    if len(plans) <= limit:
        return plans
    selected: list[Any] = []
    seen_groups: set[str] = set()
    for plan in plans:
        group = preview_group_key(plan)
        if group in seen_groups:
            continue
        selected.append(plan)
        seen_groups.add(group)
        if len(selected) >= limit:
            return selected
    if len(seen_groups) <= 1:
        return plans[:limit]
    for plan in plans:
        if plan in selected:
            continue
        selected.append(plan)
        if len(selected) >= limit:
            break
    return selected


def preview_spans_multiple_groups(plans: list[Any]) -> bool:
    groups = {preview_group_key(plan) for plan in plans}
    return len(groups) > 1


def print_run_summary(
    *,
    console: Any,
    format_path_fn: Any,
    stats: Any,
    plans: list[Any],
    errors: list[str],
    result: Any,
    cache_path: Path | None,
    report_path: Path | None,
    apply_report_path: Path | None = None,
) -> None:
    failures = len(errors) + len(result.errors)
    console.print("Summary:")
    console.print(f"Planned: {len(plans)}")
    console.print(f"Skipped: {stats.skipped}")
    for line in skip_reason_lines(stats):
        console.print(line)
    console.print(f"Cache hits: {stats.cache_hits}")
    console.print(f"Manual entries: {stats.manual}")
    console.print(f"Failures: {failures}")
    console.print(f"Elapsed: {stats.elapsed:.2f}s")
    if cache_path is not None:
        console.print(f"Cache path: {format_path_fn(cache_path)}")
    else:
        console.print("Cache path: disabled")
    if report_path is not None:
        console.print(f"Report path: {format_path_fn(report_path)}")
    if apply_report_path is not None:
        console.print(f"Apply report path: {format_path_fn(apply_report_path)}")


def skip_reason_lines(stats: Any) -> list[str]:
    labels = [
        ("filtered_media_type", "filtered by media type"),
        ("no_candidates", "no candidates"),
        ("manual_skip", "user skipped"),
        ("offline_no_cache", "offline/no cache"),
        ("conflict_skip", "conflict policy skipped"),
    ]
    parts: list[str] = []
    for attr, label in labels:
        value = int(getattr(stats, attr, 0) or 0)
        if value:
            parts.append(f"{label}={value}")
    return [f"Skip reasons: {', '.join(parts)}"] if parts else []


def plan_items(
    *,
    incoming: Path,
    library: Path,
    mode: str,
    copy_mode: bool,
    interactive: bool,
    auto_accept: bool,
    min_confidence: float,
    extensions: str,
    cache_path: Path,
    limit: int | None,
    show_cache: bool,
    media_type_filter: str | None,
    use_cache: bool,
    on_conflict: str,
    offline: bool = False,
    allow_risky_enter_accept: bool = False,
    parse_extensions_fn: Any,
    infer_item_fn: Any,
    resolve_media_type_override_fn: Any,
    safe_print_fn: Any,
    console_for_fn: Any,
    file_panel_fn: Any,
    reusable_tv_cache_safe_fn: Any,
    snapshot_stats_fn: Any,
    process_item_fn: Any,
    save_cache_fn: Any,
    record_log_event_fn: Any,
    logger: Any,
    rich_escape_fn: Any,
    progress_cls: Any,
    text_column_cls: Any,
    back_requested_exc: type[BaseException],
    cache_snapshot_cls: Any,
    history_entry_cls: Any,
    plan_stats_cls: Any,
    quiet_output: bool,
) -> tuple[list[Any], list[str], Any]:
    cache_store: Cache = Cache(cache_path) if use_cache else NullCache()
    exts = parse_extensions_fn(extensions)
    files = iter_video_files(incoming, exts)
    if limit:
        files = files[:limit]

    plans: list[Any] = []
    errors: list[str] = []
    stats = plan_stats_cls()
    started = time.monotonic()
    planned: dict[str, int] = {}
    collisions = 0
    history: list[Any] = []
    episode_cache = EpisodeCache()
    media_type_overrides: dict[str, str] = {}
    tv_search_cache: dict[str, list[tvmaze.TVMazeShow]] = {}
    movie_entity_cache: dict[str, wikidata.WikidataFilm] = {}

    with cache_store.batch():
        with progress_cls(
            text_column_cls("{task.completed}/{task.total} - {task.description}"),
            disable=interactive or not sys.stdout.isatty(),
        ) as progress:
            task = progress.add_task("Planning files...", total=len(files))
            with tvmaze.create_session() as session_tv, wikidata.create_session() as session_wd:
                total = len(files)
                index = 0
                while index < len(files):
                    path = files[index]
                    progress.update(task, completed=min(index + 1, total), description=f"Planning: {rich_escape_fn(path.name)}")
                    try:
                        item = infer_item_fn(path)
                        item, override_key = resolve_media_type_override_fn(item, cache_store, incoming, media_type_overrides)
                        record_log_event_fn(
                            logger,
                            "file_inferred",
                            level=10,
                            path=path,
                            media_type=item.media_type,
                            title=item.title,
                            year=item.year,
                            season=item.season,
                            episode=item.episode,
                        )
                        if media_type_filter and item.media_type != media_type_filter:
                            safe_print_fn(
                                f"Skipped: {rich_escape_fn(path.name)} inferred as {item.media_type}, "
                                f"but run is filtered to {media_type_filter}.",
                                progress,
                            )
                            stats.skipped += 1
                            if hasattr(stats, "filtered_media_type"):
                                stats.filtered_media_type += 1
                            record_log_event_fn(
                                logger,
                                "file_filtered_by_media_type",
                                path=path,
                                inferred_media_type=item.media_type,
                                requested_media_type=media_type_filter,
                                title=item.title,
                                year=item.year,
                                season=item.season,
                                episode=item.episode,
                            )
                            index += 1
                            continue
                        if not quiet_output:
                            safe_print_fn("", progress)
                            console_for_fn(progress).rule()
                            safe_print_fn(file_panel_fn(index + 1, total, item, incoming), progress)
                        cache_key = build_cache_key(item.path, incoming, item.media_type, item.year)
                        cache_snapshots: list[Any] = []
                        if override_key:
                            cache_snapshots.append(cache_snapshot_cls("show", override_key, cache_store.get_show(override_key)))
                        if item.media_type == "tv":
                            reusable_safe = reusable_tv_cache_safe_fn(item)
                            reusable_show_key = tv_show_cache_key(item.title, item.year) if reusable_safe else None
                            folder_show_key = tv_show_folder_cache_key(item.path, incoming)
                            keys = [cache_key]
                            if reusable_show_key:
                                keys.append(reusable_show_key)
                            if folder_show_key:
                                keys.append(folder_show_key)
                            if reusable_safe and item.season is not None and item.episode is not None:
                                keys.append(tv_episode_cache_key(item.title, item.year, item.season, item.episode))
                            for key in keys:
                                cache_snapshots.append(cache_snapshot_cls("show", key, cache_store.get_show(key)))
                        else:
                            reusable_movie_key = movie_cache_key(item.title, item.year)
                            for key in [cache_key, reusable_movie_key]:
                                cache_snapshots.append(cache_snapshot_cls("movie", key, cache_store.get_movie(key)))
                        stats_snapshot = snapshot_stats_fn(stats)
                        errors_len = len(errors)
                        result = process_item_fn(
                            item=item,
                            library=library,
                            cache=cache_store,
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
                            incoming_root=incoming,
                            planned=planned,
                            on_conflict=on_conflict,
                            allow_back=bool(history),
                            offline=offline,
                            allow_risky_enter_accept=allow_risky_enter_accept,
                            media_type_overrides=media_type_overrides,
                            tv_search_cache=tv_search_cache,
                            movie_entity_cache=movie_entity_cache,
                        )
                        if result is None:
                            plan, collision = None, False
                        else:
                            plan, collision = result
                        history.append(
                            history_entry_cls(
                                index=index,
                                plan=plan,
                                collision=collision,
                                cache_snapshots=cache_snapshots,
                                stats_snapshot=stats_snapshot,
                                errors_len=errors_len,
                            )
                        )
                        if plan:
                            plans.append(plan)
                            if collision:
                                collisions += 1
                        index += 1
                    except back_requested_exc:
                        if not history:
                            safe_print_fn("No previous decision to return to.", progress)
                            continue
                        entry = history.pop()
                        if entry.plan:
                            if plans and plans[-1] == entry.plan:
                                plans.pop()
                            else:
                                try:
                                    plans.remove(entry.plan)
                                except ValueError:
                                    pass
                            key = str(entry.plan.destination).lower()
                            if key in planned:
                                if planned[key] <= 1:
                                    planned.pop(key, None)
                                else:
                                    planned[key] -= 1
                        if entry.collision and collisions > 0:
                            collisions -= 1
                        for snapshot in entry.cache_snapshots:
                            if snapshot.section == "show":
                                if snapshot.previous is None:
                                    cache_store.delete_show(snapshot.key)
                                else:
                                    cache_store.set_show(snapshot.key, snapshot.previous)
                            else:
                                if snapshot.previous is None:
                                    cache_store.delete_movie(snapshot.key)
                                else:
                                    cache_store.set_movie(snapshot.key, snapshot.previous)
                        save_cache_fn(cache_store, progress)
                        stats.auto_matched = entry.stats_snapshot.auto_matched
                        stats.user_confirmed = entry.stats_snapshot.user_confirmed
                        stats.manual = entry.stats_snapshot.manual
                        stats.skipped = entry.stats_snapshot.skipped
                        stats.filtered_media_type = getattr(entry.stats_snapshot, "filtered_media_type", 0)
                        stats.no_candidates = getattr(entry.stats_snapshot, "no_candidates", 0)
                        stats.manual_skip = getattr(entry.stats_snapshot, "manual_skip", 0)
                        stats.offline_no_cache = getattr(entry.stats_snapshot, "offline_no_cache", 0)
                        stats.conflict_skip = getattr(entry.stats_snapshot, "conflict_skip", 0)
                        stats.errors = entry.stats_snapshot.errors
                        stats.cache_hits = entry.stats_snapshot.cache_hits
                        stats.elapsed = entry.stats_snapshot.elapsed
                        del errors[entry.errors_len:]
                        index = entry.index
                        back_path = files[index]
                        progress.update(
                            task,
                            completed=min(index + 1, total),
                            description=f"Planning: {rich_escape_fn(back_path.name)}",
                        )
                        safe_print_fn("Rewound to previous file.", progress)
                    except (OSError, ValueError, RuntimeError, requests.RequestException) as exc:
                        logger.exception("planning_failed", extra={"path": path})
                        stats.errors += 1
                        errors.append(f"{path}: {exc}")
                        index += 1

    stats.elapsed = time.monotonic() - started
    if collisions:
        safe_print_fn(f"{collisions} collision(s) resolved by suffixing (2), (3), ...", None)
    return plans, errors, stats


def tv_candidates(
    *,
    item: InferredItem,
    session: requests.Session,
    cache: Any,
    show_cache: bool,
    incoming_root: Path | None = None,
    cache_key: str | None = None,
    offset: int = 0,
    raw_results: list[Any] | None = None,
    search_query: str | None = None,
    progress: Any = None,
    limit: int = 5,
    offline: bool = False,
    interactive: bool = False,
    search_cache: dict[str, list[Any]] | None = None,
    reusable_tv_cache_safe_fn: Any = None,
    tv_show_cache_key_fn: Any = None,
    tv_episode_cache_key_fn: Any = None,
    tv_show_folder_cache_key_fn: Any = None,
    cache_entry_confirmed_or_auto_fn: Any = None,
    cache_entry_compatible_fn: Any = None,
    log_event_fn: Any = None,
    logger: Any = None,
    safe_print_fn: Any = None,
    rich_escape_fn: Any = None,
    candidate_cls: Any = None,
    candidate_page_cls: Any = None,
    tv_candidate_from_show_fn: Any = None,
    make_search_query_fn: Any = None,
    tv_search_cache_key_fn: Any = None,
    normalize_tv_retry_query_fn: Any = None,
    year_distance_fn: Any = None,
) -> Any:
    path_key = cache_key or item.title
    reusable_safe = reusable_tv_cache_safe_fn(item)
    reusable_show_key = tv_show_cache_key_fn(item.title, item.year) if reusable_safe else None
    reusable_episode_key = None
    folder_show_key = tv_show_folder_cache_key_fn(item.path, incoming_root)
    if reusable_safe and item.season is not None and item.episode is not None:
        reusable_episode_key = tv_episode_cache_key_fn(item.title, item.year, item.season, item.episode)
    cached = None
    cached_key = None
    candidate_keys: list[str] = []
    if reusable_episode_key:
        candidate_keys.append(reusable_episode_key)
    if reusable_show_key:
        candidate_keys.append(reusable_show_key)
    if folder_show_key:
        candidate_keys.append(folder_show_key)
    candidate_keys.append(path_key)

    for key in candidate_keys:
        possible = cache.get_show(key)
        if not possible:
            continue
        if not cache_entry_confirmed_or_auto_fn(possible):
            continue
        if not possible.get("manual") and not cache_entry_compatible_fn(item.year, possible.get("premiered")):
            continue
        cached = possible
        cached_key = key
        break
    results: list[Any] = []
    elapsed = 0.0
    total_time = None
    if cached:
        log_event_fn(
            logger,
            "cache_hit",
            cache_scope="tv",
            cache_key=cached_key,
            path=item.path,
            media_type=item.media_type,
            title=item.title,
            query=None,
            selection_mode=None,
            selection_source="cache",
            decision_reason="cache_lookup",
            confidence=None,
        )
        if show_cache:
            name = cached.get("name") or item.title
            year = cached.get("chosen_year") or cached.get("premiered")
            year_text = f" ({year})" if year else ""
            safe_print_fn("Cache hit.", progress)
            if cached_key == reusable_show_key:
                safe_print_fn("Cache type: REUSABLE", progress)
                safe_print_fn(
                    f"Using cached show match: {name}{year_text} [TVMaze]. Using inferred S/E for this file.",
                    progress,
                )
            elif cached_key == folder_show_key:
                safe_print_fn("Cache type: FOLDER", progress)
                safe_print_fn(
                    f"Using cached show match for folder: {name}{year_text} [TVMaze]. Using inferred S/E for this file.",
                    progress,
                )
            else:
                safe_print_fn("Cache type: FILE-SPECIFIC", progress)
                safe_print_fn(
                    f"Using cached match for: {rich_escape_fn(item.path.name)} -> {rich_escape_fn(name)}{year_text} [TVMaze]",
                    progress,
                )
        if cached.get("manual"):
            metadata: dict[str, Any] = {
                "id": None,
                "name": cached.get("name") or item.title,
                "year": cached.get("chosen_year") or cached.get("premiered"),
                "manual": True,
            }
            if cached_key == folder_show_key and cached.get("season") is not None:
                metadata["season"] = cached.get("season")
            if cached_key not in {reusable_show_key, folder_show_key}:
                if "season" in cached:
                    metadata["season"] = cached.get("season")
                if "episode" in cached:
                    metadata["episode"] = cached.get("episode")
                if "episode_title" in cached:
                    metadata["episode_title"] = cached.get("episode_title")
            candidate = candidate_cls(
                title=metadata["name"],
                year=metadata.get("year"),
                source="Manual",
                confidence=1.0,
                metadata=metadata,
            )
        else:
            show = tvmaze.TVMazeShow(id=int(cached["id"]), name=cached["name"], premiered=cached.get("premiered"))
            candidate = tv_candidate_from_show_fn(item, show)
        if cached_key not in {reusable_show_key, folder_show_key}:
            candidate.metadata["season"] = cached.get("season")
            candidate.metadata["episode"] = cached.get("episode")
            candidate.metadata["episode_title"] = cached.get("episode_title")
        results.append(candidate)
        return candidate_page_cls(
            candidates=results,
            raw_results=None,
            next_offset=0,
            has_more=False,
            cache_hit=True,
            cache_reusable=cached_key in {reusable_show_key, reusable_episode_key},
        )

    if offline:
        log_event_fn(
            logger,
            "offline_no_cached_match",
            media_type=item.media_type,
            path=item.path,
            title=item.title,
            query=search_query,
            selection_mode=None,
            selection_source="offline",
            decision_reason="offline_no_cached_match",
            confidence=None,
            cache_scope="tv",
        )
        return candidate_page_cls(candidates=[], raw_results=[], next_offset=0, has_more=False)

    if raw_results is None:
        query = search_query or make_search_query_fn(item.title) or item.title
        cache_lookup_key = tv_search_cache_key_fn(query, item.year)
        if search_cache is not None and cache_lookup_key in search_cache:
            raw_results = search_cache[cache_lookup_key]
            elapsed = 0.0
            total_time = 0.0
        else:
            log_event_fn(
                logger,
                "candidate_search_started",
                source="TVMaze",
                query=query,
                media_type=item.media_type,
                path=item.path,
            )
            safe_print_fn(f"Searching TVMaze for: {rich_escape_fn(query)}", progress)
            total_started = time.monotonic()
            started = total_started
            raw_results = tvmaze.search_shows(query, session=session, raise_on_error=interactive)
            elapsed = time.monotonic() - started
            total_time = time.monotonic() - total_started
            if not raw_results:
                retry_query = normalize_tv_retry_query_fn(search_query or item.title)
                if retry_query and retry_query != query:
                    safe_print_fn(f"Retrying TVMaze with normalized query: {rich_escape_fn(retry_query)}", progress)
                    started_retry = time.monotonic()
                    raw_results = tvmaze.search_shows(retry_query, session=session, raise_on_error=interactive)
                    elapsed += time.monotonic() - started_retry
                    total_time = time.monotonic() - total_started
                    cache_lookup_key = tv_search_cache_key_fn(retry_query, item.year)
            log_event_fn(
                logger,
                "candidate_search_finished",
                source="TVMaze",
                query=query,
                media_type=item.media_type,
                path=item.path,
                result_count=len(raw_results),
                duration_ms=int(total_time * 1000),
            )
            if search_cache is not None:
                search_cache[cache_lookup_key] = raw_results
            if not raw_results:
                safe_print_fn(f"No candidates (api={elapsed:.2f}s).", progress)
                return candidate_page_cls(
                    candidates=[],
                    raw_results=raw_results,
                    next_offset=0,
                    has_more=False,
                    search_time=elapsed,
                    total_time=total_time,
                )
    page = raw_results[offset : offset + limit]
    for show in page:
        results.append(tv_candidate_from_show_fn(item, show))
    results.sort(key=lambda cand: (-cand.confidence, year_distance_fn(item.year, cand.year)))
    next_offset = offset + limit
    has_more = next_offset < len(raw_results)
    if raw_results is not None and offset == 0:
        best = results[0].confidence if results else 0.0
        total_text = f"{total_time:.2f}s" if total_time is not None else f"{elapsed:.2f}s"
        safe_print_fn(
            f"Found {len(results)} candidates (best confidence {best:.2f}, api={elapsed:.2f}s, total={total_text}).",
            progress,
        )
    return candidate_page_cls(candidates=results, raw_results=raw_results, next_offset=next_offset, has_more=has_more)


def movie_candidates(
    *,
    item: InferredItem,
    session: requests.Session,
    cache: Any,
    show_cache: bool,
    cache_key: str | None = None,
    offset: int = 0,
    raw_results: list[Any] | None = None,
    search_query: str | None = None,
    progress: Any = None,
    limit: int = 5,
    offline: bool = False,
    interactive: bool = False,
    movie_entity_cache: dict[str, wikidata.WikidataFilm] | None = None,
    movie_cache_key_fn: Any = None,
    reusable_movie_cache_safe_fn: Any = None,
    cache_entry_confirmed_or_auto_fn: Any = None,
    cache_entry_compatible_fn: Any = None,
    log_event_fn: Any = None,
    logger: Any = None,
    safe_print_fn: Any = None,
    rich_escape_fn: Any = None,
    movie_candidate_from_film_fn: Any = None,
    candidate_page_cls: Any = None,
    build_movie_fallback_queries_fn: Any = None,
    make_search_query_fn: Any = None,
    year_distance_fn: Any = None,
) -> Any:
    path_key = cache_key or item.title
    reusable_key = movie_cache_key_fn(item.title, item.year)
    cached = None
    cached_key = None
    candidate_keys: list[str] = []
    if reusable_movie_cache_safe_fn(item):
        candidate_keys.append(reusable_key)
    candidate_keys.append(path_key)
    for key in candidate_keys:
        possible = cache.get_movie(key)
        if not possible:
            continue
        if bool(possible.get("manual")):
            continue
        if not cache_entry_confirmed_or_auto_fn(possible):
            continue
        if not cache_entry_compatible_fn(item.year, possible.get("year")):
            continue
        cached = possible
        cached_key = key
        break
    results: list[Any] = []
    elapsed = 0.0
    fetch_time = 0.0
    total_time = None
    if cached and not cached.get("manual"):
        log_event_fn(
            logger,
            "cache_hit",
            cache_scope="movie",
            cache_key=cached_key,
            path=item.path,
            media_type=item.media_type,
            title=item.title,
            query=search_query,
            selection_mode=None,
            selection_source="cache",
            decision_reason="cache_lookup",
            confidence=None,
        )
        if show_cache:
            title = cached.get("title") or item.title
            year = cached.get("year")
            year_text = f" ({year})" if year else ""
            safe_print_fn("Cache hit.", progress)
            if cached_key == reusable_key:
                safe_print_fn("Cache type: REUSABLE", progress)
            else:
                safe_print_fn("Cache type: FILE-SPECIFIC", progress)
            safe_print_fn(
                f"Using cached match for: {rich_escape_fn(item.path.name)} -> {rich_escape_fn(title)}{year_text} [Wikidata]",
                progress,
            )
        film = wikidata.WikidataFilm(qid=cached["qid"], title=cached["title"], year=cached.get("year"), is_film=True)
        results.append(movie_candidate_from_film_fn(item, film))
        return candidate_page_cls(
            candidates=results,
            raw_results=None,
            next_offset=0,
            has_more=False,
            cache_hit=True,
            cache_reusable=cached_key == reusable_key,
        )

    if offline:
        log_event_fn(
            logger,
            "offline_no_cached_match",
            media_type=item.media_type,
            path=item.path,
            title=item.title,
            query=search_query,
            selection_mode=None,
            selection_source="offline",
            decision_reason="offline_no_cached_match",
            confidence=None,
            cache_scope="movie",
        )
        return candidate_page_cls(candidates=[], raw_results=[], next_offset=0, has_more=False)

    if raw_results is None:
        fallback_queries = build_movie_fallback_queries_fn(item.title, None, item.year)
        queries: list[str] = []
        if search_query and search_query.strip():
            queries.append(search_query.strip())
        queries.extend(fallback_queries)
        if not queries:
            base_query = make_search_query_fn(item.title) or item.title
            if base_query:
                queries.append(base_query)
        has_meaningful_title = bool((item.title or "").strip())
        if not queries and not has_meaningful_title:
            queries.append("unknown")
        deduped_queries: list[str] = []
        seen_queries: set[str] = set()
        for query in queries:
            marker = query.casefold()
            if marker in seen_queries:
                continue
            seen_queries.add(marker)
            deduped_queries.append(query)
        queries = deduped_queries
        if not queries:
            return candidate_page_cls(
                candidates=[],
                raw_results=[],
                next_offset=0,
                has_more=False,
                search_time=0.0,
                total_time=0.0,
            )
        query = queries[0]
        log_event_fn(
            logger,
            "candidate_search_started",
            source="Wikidata",
            query=query,
            query_attempts=len(queries),
            media_type=item.media_type,
            path=item.path,
        )
        safe_print_fn(f"Searching Wikidata for: {rich_escape_fn(query)}", progress)
        total_started = time.monotonic()
        attempts = 0
        raw_results = []
        for current_query in queries:
            attempts += 1
            if attempts > 1:
                safe_print_fn(f"Retrying Wikidata with simplified query: {rich_escape_fn(current_query)}", progress)
            started = time.monotonic()
            attempt_results = wikidata.search(current_query, session=session, limit=10, raise_on_error=interactive)
            elapsed += time.monotonic() - started
            query = current_query
            if attempt_results:
                raw_results = attempt_results
                break
        if not raw_results:
            total_time = time.monotonic() - total_started
            safe_print_fn(f"No candidates (api={total_time:.2f}s).", progress)
            return candidate_page_cls(
                candidates=[],
                raw_results=raw_results,
                next_offset=0,
                has_more=False,
                search_time=elapsed,
                total_time=total_time,
            )
        total_time = time.monotonic() - total_started
        log_event_fn(
            logger,
            "candidate_search_finished",
            source="Wikidata",
            query=query,
            query_attempts=attempts,
            media_type=item.media_type,
            path=item.path,
            result_count=len(raw_results),
            duration_ms=int(total_time * 1000),
        )
    def _candidate_year(candidate: Any) -> int | None:
        description = candidate.description if isinstance(candidate.description, str) else ""
        match = WIKIDATA_DESCRIPTION_YEAR_RE.search(description)
        if match:
            return int(match.group(1))
        return None

    def _provisional_confidence(candidate: Any) -> tuple[float, int]:
        year = _candidate_year(candidate)
        film = wikidata.WikidataFilm(qid=candidate.qid, title=candidate.label, year=year, is_film=True)
        provisional = movie_candidate_from_film_fn(item, film, description=candidate.description)
        distance = year_distance_fn(item.year, provisional.year)
        return provisional.confidence, -distance

    if raw_results is not None:
        raw_results = sorted(raw_results, key=_provisional_confidence, reverse=True)
    idx = offset
    fetch_started = time.monotonic()
    while idx < len(raw_results) and len(results) < limit:
        cand = raw_results[idx]
        idx += 1
        if movie_entity_cache is not None and cand.qid in movie_entity_cache:
            film = movie_entity_cache[cand.qid]
        else:
            film = wikidata.fetch_entity(cand.qid, session=session)
            if movie_entity_cache is not None:
                movie_entity_cache[cand.qid] = film
        if not film.is_film:
            continue
        results.append(movie_candidate_from_film_fn(item, film, description=cand.description))
    fetch_time = time.monotonic() - fetch_started
    results.sort(key=lambda cand: (-cand.confidence, year_distance_fn(item.year, cand.year)))
    has_more = idx < len(raw_results)
    if raw_results is not None and offset == 0:
        best = results[0].confidence if results else 0.0
        if total_time is None:
            total_time = elapsed + fetch_time
        else:
            total_time = total_time + fetch_time
        safe_print_fn(
            f"Found {len(results)} candidates (best confidence {best:.2f}, "
            f"api={elapsed:.2f}s, fetch={fetch_time:.2f}s, total={total_time:.2f}s).",
            progress,
        )
    return candidate_page_cls(
        candidates=results,
        raw_results=raw_results,
        next_offset=idx,
        has_more=has_more,
        search_time=elapsed,
        fetch_time=fetch_time,
        total_time=total_time,
    )
