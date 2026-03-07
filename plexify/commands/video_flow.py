from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

import requests

from ..cache import Cache, NullCache
from ..infer import InferredItem
from ..sources import tvmaze, wikidata
from ..tv_episode_cache import EpisodeCache
from ..util import build_cache_key, iter_video_files, movie_cache_key, tv_episode_cache_key, tv_show_cache_key, tv_show_folder_cache_key
from ..cache_policy import is_ambiguous_cache_title


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
