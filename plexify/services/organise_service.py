from __future__ import annotations

import sys
from pathlib import Path

from ..configuration import state_paths
from typing import Any


def _print_complete_plan(console: Any, plans: list[Any], *, on_conflict: str, copy_mode: bool, format_path_fn: Any, heading: str) -> None:
    console.print(heading)
    tv_groups: dict[tuple[str, Any], list[Any]] = {}
    ordered: list[tuple[str, Any]] = []
    for plan in plans:
        if plan.media_type == "tv":
            metadata = plan.metadata
            key = (str(metadata.get("show") or "Unknown show"), metadata.get("year"))
            if key not in tv_groups:
                tv_groups[key] = []
                ordered.append(("tv", key))
            tv_groups[key].append(plan)
        else:
            ordered.append(("movie", plan))

    def warning_for(plan: Any) -> str:
        selection = plan.metadata.get("selection") or {}
        if not isinstance(selection, dict):
            return ""
        warnings = []
        if selection.get("risky_search_query"):
            warnings.append("broadened/risky search")
        if selection.get("fallback_attempts"):
            warnings.append(f"{selection['fallback_attempts']} fallback search(es)")
        if selection.get("year_mismatch"):
            warnings.append(f"year mismatch: filename {selection.get('filename_year')}, provider {selection.get('provider_year')}")
        confidence = selection.get("confidence")
        suffix = f"confidence {confidence:.3f}" if isinstance(confidence, (int, float)) else ""
        if warnings:
            suffix = (suffix + "; " if suffix else "") + "warning: " + ", ".join(warnings)
        return suffix

    action = "COPY" if copy_mode else "MOVE"
    lifecycle = "source preserved" if copy_mode else "source removed after successful move"
    for kind, value in ordered:
        if kind == "tv":
            show, year = value
            group = tv_groups[value]
            label = f"{show} ({year})" if year else show
            lifecycle = "COPY / source preserved" if copy_mode else "MOVE / source removed after successful move"
            console.print(f"{label} — {len(group)} episode(s) | {lifecycle} | conflict policy {on_conflict}:")
            for plan in sorted(group, key=lambda item: (item.metadata.get("season") or 0, item.metadata.get("episode") or 0, str(item.source))):
                meta = plan.metadata
                start = int(meta.get("episode") or 0)
                end = meta.get("episode_end")
                episode = f"S{int(meta.get('season') or 0):02d}E{start:02d}"
                if end and int(end) > start:
                    episode += f"-E{int(end):02d}"
                details = warning_for(plan)
                conflict = "destination exists and will be overwritten" if plan.destination.exists() and on_conflict == "overwrite" else f"conflict: {on_conflict}"
                detail = f" | {details}" if details else ""
                console.print(f"  {episode} | {format_path_fn(plan.source)} → {format_path_fn(plan.destination)} | {conflict}{detail}")
            continue
        plan = value
        metadata = plan.metadata
        identity = metadata.get("title") or "Unknown title"
        if metadata.get("year"):
            identity = f"{identity} ({metadata['year']})"
        details = warning_for(plan)
        detail = f" | {details}" if details else ""
        conflict = "existing destination will be overwritten" if plan.destination.exists() and on_conflict == "overwrite" else f"conflict policy {on_conflict}"
        console.print(f"{identity}{detail}")
        console.print(f"  SOURCE: {format_path_fn(plan.source)}")
        console.print(f"  {action} → DESTINATION: {format_path_fn(plan.destination)}")
        console.print(f"  Conflict: {conflict} | {lifecycle}")

def run_video_workflow(
    *,
    options: Any,
    console: Any,
    plan_items_fn: Any,
    select_preview_plans_fn: Any,
    preview_spans_multiple_groups_fn: Any,
    confirm_move_fn: Any,
    confirm_fn: Any,
    confirm_overwrite_apply_fn: Any,
    apply_with_streamed_report_fn: Any,
    execute_plans_fn: Any,
    prune_empty_dirs_fn: Any,
    parse_prune_ignore_fn: Any,
    write_report_fn: Any,
    print_run_summary_fn: Any,
    build_command_config_cls: Any,
    build_command_fn: Any,
    parse_extensions_fn: Any,
    format_path_fn: Any,
    now_timestamp_fn: Any,
    log_event_fn: Any,
    logger: Any,
    typer_module: Any,
) -> None:
    incoming = options.incoming
    library = options.library
    mode = options.mode
    copy_mode = options.copy_mode
    extensions = options.extensions
    min_confidence = options.min_confidence
    cache = options.cache
    report = options.report
    yes = options.yes
    limit = options.limit
    print_tree = options.print_tree
    interactive_mode = options.interactive_mode
    media_type = options.media_type
    no_cache = options.no_cache
    clear_cache = options.clear_cache
    offline = options.offline
    quiet = options.quiet
    on_conflict = options.on_conflict
    prune_empty_dirs = options.prune_empty_dirs
    prune_ignore = options.prune_ignore
    allow_risky_enter_accept = options.allow_risky_enter_accept
    strict_safe = options.strict_safe
    plain_output = options.plain_output
    platform = getattr(options, "platform", "auto")
    run_id = options.run_id

    ignored_prune_files = parse_prune_ignore_fn(prune_ignore)
    default_cache_path, reports_dir = state_paths(library)
    cache_path = cache or default_cache_path
    report_path = report or reports_dir / f"{now_timestamp_fn()}.json"
    if clear_cache:
        cache_path.unlink(missing_ok=True)

    media_type_filter = None if media_type == "auto" else media_type
    planning_library = library.parent if getattr(options, "category_root", False) else library
    plans, errors, stats = plan_items_fn(
        incoming=incoming,
        library=planning_library,
        mode=mode,
        copy_mode=copy_mode,
        interactive=interactive_mode,
        auto_accept=yes,
        min_confidence=min_confidence,
        extensions=extensions,
        cache_path=cache_path,
        limit=limit,
        show_cache=interactive_mode or print_tree,
        media_type_filter=media_type_filter,
        use_cache=not no_cache,
        on_conflict=on_conflict,
        offline=offline,
        allow_risky_enter_accept=allow_risky_enter_accept,
    )
    verified_existing = list(getattr(stats, "semantic_duplicate_plans", []))

    if print_tree and plans:
        tree = options.build_tree_fn([plan.destination for plan in plans])
        console.print(tree)

    apply_mode = mode == "apply"
    if apply_mode and interactive_mode:
        console.print("Plan summary:")
        console.print(f"Planned items: {len(plans)}")
        console.print(f"Skipped: {stats.skipped}")
        console.print(
            "Selection policy: "
            f"auto_accept={'on' if yes else 'off'}, "
            f"allow_risky_enter_accept={'on' if allow_risky_enter_accept else 'off'}, "
            f"min_confidence={min_confidence:.2f}"
        )
        for line in options.skip_reason_lines_fn(stats):
            console.print(line)
        console.print(f"Errors: {stats.errors + len(errors)}")
        _print_complete_plan(console, plans, on_conflict=on_conflict, copy_mode=copy_mode, format_path_fn=format_path_fn, heading="Complete filesystem plan:")
        if not copy_mode:
            console.print("Warning: move will remove the original files from the incoming folder.")
            if not confirm_move_fn(None):
                console.print("Cancelled. No filesystem changes were made.")
                return
        else:
            if not confirm_fn("Apply this plan now? [y/N]", False, None, show_default=False):
                console.print("Cancelled. No filesystem changes were made.")
                return
    if apply_mode and plans and on_conflict == "overwrite":
        if not interactive_mode and not sys.stdin.isatty():
            console.print("Overwrite mode requires an interactive confirmation token (OVERWRITE).")
            raise typer_module.Exit(code=2)
        if not confirm_overwrite_apply_fn(plans, copy_mode):
            console.print("Cancelled. No filesystem changes were made.")
            return
    if apply_mode and (plans or (copy_mode and verified_existing)):
        result = apply_with_streamed_report_fn(
            plans,
            copy_mode=copy_mode,
            on_conflict=on_conflict,
            report_path=report_path,
            verified_existing=verified_existing,
        )
    else:
        result = execute_plans_fn(plans, apply=apply_mode, copy_mode=copy_mode, on_conflict=on_conflict)

    if prune_empty_dirs and not copy_mode and plans:
        if apply_mode:
            prune_empty_dirs_fn(result.moved, incoming, dry_run=False, ignored_files=ignored_prune_files)
        else:
            prune_empty_dirs_fn(plans, incoming, dry_run=True, ignored_files=ignored_prune_files)

    if not apply_mode:
        write_report_fn(report_path, plans, mode, copy_mode)
    elif not plans and not verified_existing:
        write_report_fn(report_path, [], mode, copy_mode)
    print_run_summary_fn(
        stats=stats,
        plans=plans,
        errors=errors,
        result=result,
        cache_path=None if no_cache else cache_path,
        report_path=report_path,
        copy_mode=copy_mode,
        mode=mode,
    )

    apply_report_path = None
    if not apply_mode and interactive_mode and (plans or (copy_mode and verified_existing)):
        _print_complete_plan(console, plans, on_conflict=on_conflict, copy_mode=copy_mode, format_path_fn=format_path_fn, heading="Complete filesystem plan before approval:")
        if confirm_fn("Apply this complete plan now? [y/N]", False, None, show_default=False):
            if on_conflict == "overwrite" and not confirm_overwrite_apply_fn(plans, copy_mode):
                console.print("Cancelled. No filesystem changes were made.")
                return
            elif not copy_mode:
                console.print("Warning: move will remove the original files from the incoming folder.")
                if not confirm_move_fn(None):
                    console.print("Cancelled. No filesystem changes were made.")
                    return
                else:
                    apply_report_path = reports_dir / f"{now_timestamp_fn()}.json"
                    result = apply_with_streamed_report_fn(
                        plans, copy_mode=copy_mode, on_conflict=on_conflict, report_path=apply_report_path,
                        verified_existing=list(getattr(stats, "semantic_duplicate_plans", [])),
                    )
                    if prune_empty_dirs:
                        prune_empty_dirs_fn(result.moved, incoming, dry_run=False, ignored_files=ignored_prune_files)
            else:
                apply_report_path = reports_dir / f"{now_timestamp_fn()}.json"
                result = apply_with_streamed_report_fn(
                    plans, copy_mode=copy_mode, on_conflict=on_conflict, report_path=apply_report_path,
                    verified_existing=list(getattr(stats, "semantic_duplicate_plans", [])),
                )
            print_run_summary_fn(
                stats=stats,
                plans=plans,
                errors=errors,
                result=result,
                cache_path=None if no_cache else cache_path,
                report_path=report_path,
                apply_report_path=apply_report_path,
                copy_mode=copy_mode,
                mode="apply",
            )
        else:
            console.print("Dry-run complete. Nothing was published.")

    if apply_report_path is not None:
        console.print(f"Apply report: {format_path_fn(apply_report_path)}")

    if result.errors or errors:
        log_event_fn(
            logger,
            "run_finished",
            run_id=run_id,
            command="organise",
            status="error",
            planned_count=len(plans),
            skipped_count=stats.skipped,
            error_count=len(result.errors) + len(errors),
            elapsed_seconds=stats.elapsed,
            applied=apply_mode,
        )
        console.print("Errors:")
        for error in result.errors + errors:
            console.print(f"- {options.rich_escape_fn(error)}")
        raise typer_module.Exit(code=1)
    if not plans and not verified_existing:
        log_event_fn(
            logger,
            "run_finished",
            run_id=run_id,
            command="organise",
            status="empty",
            planned_count=0,
            skipped_count=stats.skipped,
            error_count=0,
            elapsed_seconds=stats.elapsed,
            applied=apply_mode,
        )
        raise typer_module.Exit(code=1)
    log_event_fn(
        logger,
        "run_finished",
        run_id=run_id,
        command="organise",
        status="success",
        planned_count=len(plans),
        skipped_count=stats.skipped,
        error_count=0,
        elapsed_seconds=stats.elapsed,
        applied=apply_mode,
    )
    return
