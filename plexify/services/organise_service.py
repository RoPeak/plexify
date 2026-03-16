from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


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
    run_id = options.run_id

    ignored_prune_files = parse_prune_ignore_fn(prune_ignore)
    cache_path = cache or library / ".plexify" / "cache.json"
    report_path = report or library / ".plexify" / "reports" / f"{now_timestamp_fn()}.json"
    if clear_cache:
        cache_path.unlink(missing_ok=True)

    media_type_filter = None if media_type == "auto" else media_type
    plans, errors, stats = plan_items_fn(
        incoming=incoming,
        library=library,
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

    if print_tree and plans:
        tree = options.build_tree_fn([plan.destination for plan in plans])
        console.print(tree)

    apply_mode = mode == "apply"
    if apply_mode and interactive_mode:
        console.print("Plan summary:")
        console.print(f"Planned items: {len(plans)}")
        console.print(f"Skipped: {stats.skipped}")
        for line in options.skip_reason_lines_fn(stats):
            console.print(line)
        console.print(f"Errors: {stats.errors + len(errors)}")
        preview = select_preview_plans_fn(plans, limit=5)
        if preview:
            if preview_spans_multiple_groups_fn(preview):
                console.print("Preview (sampled across shows/titles):")
            else:
                console.print("Preview:")
            for plan in preview:
                console.print(f"FROM: {format_path_fn(plan.source)}")
                console.print(f"TO:   {format_path_fn(plan.destination)}")
        if not copy_mode:
            console.print("Warning: move will remove the original files from the incoming folder.")
            if not confirm_move_fn(None):
                console.print("Cancelled. No changes were made.")
                raise typer_module.Exit(code=0)
        else:
            if not confirm_fn("Apply this plan now? [y/N]", False, None, show_default=False):
                console.print("Cancelled. No changes were made.")
                raise typer_module.Exit(code=0)
    if apply_mode and plans and on_conflict == "overwrite":
        if not interactive_mode and not sys.stdin.isatty():
            console.print("Overwrite mode requires an interactive confirmation token (OVERWRITE).")
            raise typer_module.Exit(code=2)
        if not confirm_overwrite_apply_fn(plans, copy_mode):
            console.print("Cancelled. No changes were made.")
            raise typer_module.Exit(code=0)
    if apply_mode and plans:
        result = apply_with_streamed_report_fn(plans, copy_mode=copy_mode, on_conflict=on_conflict, report_path=report_path)
    else:
        result = execute_plans_fn(plans, apply=apply_mode, copy_mode=copy_mode, on_conflict=on_conflict)

    if prune_empty_dirs and not copy_mode and plans:
        if apply_mode:
            prune_empty_dirs_fn(result.moved, incoming, dry_run=False, ignored_files=ignored_prune_files)
        else:
            prune_empty_dirs_fn(plans, incoming, dry_run=True, ignored_files=ignored_prune_files)

    if not apply_mode:
        write_report_fn(report_path, plans, mode, copy_mode)
    elif not plans:
        write_report_fn(report_path, [], mode, copy_mode)
    print_run_summary_fn(
        stats=stats,
        plans=plans,
        errors=errors,
        result=result,
        cache_path=None if no_cache else cache_path,
        report_path=report_path,
    )

    apply_report_path = None
    if not apply_mode and interactive_mode and plans:
        if confirm_fn("Apply these changes now? [y/N]", False, None, show_default=False):
            if on_conflict == "overwrite" and not confirm_overwrite_apply_fn(plans, copy_mode):
                console.print("Cancelled. No changes were made.")
            elif not copy_mode:
                console.print("Warning: move will remove the original files from the incoming folder.")
                if not confirm_move_fn(None):
                    console.print("Cancelled. No changes were made.")
                else:
                    apply_report_path = library / ".plexify" / "reports" / f"{now_timestamp_fn()}.json"
                    result = apply_with_streamed_report_fn(
                        plans, copy_mode=copy_mode, on_conflict=on_conflict, report_path=apply_report_path
                    )
                    if prune_empty_dirs:
                        prune_empty_dirs_fn(result.moved, incoming, dry_run=False, ignored_files=ignored_prune_files)
            else:
                apply_report_path = library / ".plexify" / "reports" / f"{now_timestamp_fn()}.json"
                result = apply_with_streamed_report_fn(
                    plans, copy_mode=copy_mode, on_conflict=on_conflict, report_path=apply_report_path
                )

    if not apply_mode:
        apply_config = build_command_config_cls(
            incoming=incoming,
            library=library,
            media_type=media_type,
            mode="apply",
            copy_mode=copy_mode,
            extensions=parse_extensions_fn(extensions),
            min_confidence=min_confidence,
            limit=limit,
            interactive=interactive_mode,
            print_tree=print_tree,
            show_enrichment=False,
            yes=yes,
            no_cache=no_cache,
            cache_file=cache,
            clear_cache=clear_cache,
            report=None,
            on_conflict=on_conflict,
            prune_empty_dirs=prune_empty_dirs,
            quiet=quiet,
            prune_ignore=prune_ignore,
            allow_risky_enter_accept=allow_risky_enter_accept,
            strict_safe=strict_safe,
        )
        console.print("Apply command:")
        console.print(build_command_fn(apply_config))
        if apply_report_path is not None:
            console.print(f"Apply report written: {format_path_fn(apply_report_path)}")

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
    if not plans:
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
    raise typer_module.Exit(code=0)
