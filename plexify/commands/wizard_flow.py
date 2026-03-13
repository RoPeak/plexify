from __future__ import annotations

from pathlib import Path
from typing import Any

from ..util import json_dump, json_load


def wizard_prefs_path() -> Path:
    return Path.home() / ".plexify" / "wizard.json"


def load_wizard_prefs() -> dict[str, dict[str, str]]:
    path = wizard_prefs_path()
    try:
        data = json_load(path)
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    cleaned: dict[str, dict[str, str]] = {}
    for key, value in data.items():
        if not isinstance(value, dict):
            continue
        cleaned[key] = {str(k): str(v) for k, v in value.items() if isinstance(k, str) and isinstance(v, str)}
    return cleaned


def save_wizard_prefs(media_key: str, source: Path, library: Path) -> None:
    prefs = load_wizard_prefs()
    prefs[media_key] = {"source": str(source), "library": str(library)}
    json_dump(wizard_prefs_path(), prefs)


def wizard_defaults(media_key: str) -> tuple[Path | None, Path | None]:
    def _sanitize(path: Path | None) -> Path | None:
        if path is None:
            return None
        try:
            expanded = path.expanduser()
            resolved = expanded.resolve(strict=False)
            if resolved == Path.cwd().resolve(strict=False):
                return None
            if not expanded.exists() or not expanded.is_dir():
                return None
        except (OSError, RuntimeError):
            return None
        return expanded

    prefs = load_wizard_prefs()
    section = prefs.get(media_key, {})
    source = _sanitize(Path(section["source"])) if "source" in section else None
    library = _sanitize(Path(section["library"])) if "library" in section else None
    return source, library


def prompt_non_overlapping_paths(
    *,
    label_source: str,
    label_library: str,
    source_default: Path | None,
    library_default: Path | None,
    prompt_path_fn: Any,
    confirm_fn: Any,
    validate_non_overlapping_fn: Any,
    console: Any,
    typer_module: Any,
) -> tuple[Path, Path]:
    source_text = prompt_path_fn(
        f"{label_source} folder",
        str(source_default) if source_default is not None else None,
        directories_only=True,
    )
    while not source_text.strip():
        console.print("Please enter a folder path.")
        source_text = prompt_path_fn(
            f"{label_source} folder",
            str(source_default) if source_default is not None else None,
            directories_only=True,
        )
    source = Path(source_text)
    while not source.exists() or not source.is_dir():
        console.print("That path does not exist or is not a folder. Please try again.")
        source_text = prompt_path_fn(
            f"{label_source} folder",
            str(source_default) if source_default is not None else None,
            directories_only=True,
        )
        while not source_text.strip():
            console.print("Please enter a folder path.")
            source_text = prompt_path_fn(
                f"{label_source} folder",
                str(source_default) if source_default is not None else None,
                directories_only=True,
            )
        source = Path(source_text)

    while True:
        library_text = prompt_path_fn(
            f"{label_library} folder",
            str(library_default) if library_default is not None else None,
            directories_only=True,
        )
        while not library_text.strip():
            console.print("Please enter a folder path.")
            library_text = prompt_path_fn(
                f"{label_library} folder",
                str(library_default) if library_default is not None else None,
                directories_only=True,
            )
        library = Path(library_text)
        if library.exists() and library.is_file():
            console.print("That path is a file. Please choose a folder path.")
            continue
        if not library.exists():
            if confirm_fn("That folder does not exist. Create it? [Y/n]", True, None, show_default=False):
                library.mkdir(parents=True, exist_ok=True)
            else:
                console.print("Cancelled. No changes were made.")
                raise typer_module.Exit(code=0)
        ok, reason, suggestion = validate_non_overlapping_fn(source, library)
        if ok:
            return source, library
        console.print(reason)
        if suggestion is not None:
            console.print(f"Suggested {label_library}: {suggestion}")
            library_default = suggestion
        if confirm_fn(f"Edit {label_source.lower()} instead? [y/N]", False, None, show_default=False):
            source_text = prompt_path_fn(
                f"{label_source} folder",
                str(source_default) if source_default is not None else None,
                directories_only=True,
            )
            while not source_text.strip():
                console.print("Please enter a folder path.")
                source_text = prompt_path_fn(
                    f"{label_source} folder",
                    str(source_default) if source_default is not None else None,
                    directories_only=True,
                )
            source = Path(source_text)
            while not source.exists() or not source.is_dir():
                console.print("That path does not exist or is not a folder. Please try again.")
                source_text = prompt_path_fn(
                    f"{label_source} folder",
                    str(source_default) if source_default is not None else None,
                    directories_only=True,
                )
                while not source_text.strip():
                    console.print("Please enter a folder path.")
                    source_text = prompt_path_fn(
                        f"{label_source} folder",
                        str(source_default) if source_default is not None else None,
                        directories_only=True,
                    )
                source = Path(source_text)


def wizard_video(
    *,
    log_level: str,
    log_format: str,
    log_file: Path | None,
    completion_enabled: bool,
    console: Any,
    wizard_defaults_fn: Any,
    prompt_non_overlapping_paths_fn: Any,
    save_wizard_prefs_fn: Any,
    detect_media_in_path_fn: Any,
    confirm_fn: Any,
    wizard_music_fn: Any,
    prompt_choice_loop_fn: Any,
    prompt_text_fn: Any,
    build_command_config_cls: Any,
    build_command_fn: Any,
    organise_options_cls: Any,
    run_organise_fn: Any,
    default_music_extensions: str,
    default_extensions_list: list[str],
    default_min_confidence: float,
    wizard_media_choices: set[str],
    wizard_mode_choices: set[str],
    wizard_copy_choices: set[str],
    default_extensions: str,
    default_prune_ignore: str,
) -> None:
    console.print("This will help you organise video files into a Plex-friendly folder layout.")
    console.print("Tip: for PowerShell tab-complete paths, run organise with --incoming/--library arguments instead.")
    if completion_enabled:
        console.print("Tip: run python -m plexify.cli --install-completion to enable shell autocompletion.")

    incoming_default, library_default = wizard_defaults_fn("video")
    incoming, library = prompt_non_overlapping_paths_fn(
        label_source="Incoming",
        label_library="Library",
        source_default=incoming_default,
        library_default=library_default,
    )
    save_wizard_prefs_fn("video", incoming, library)

    audio_exts = {ext.strip().lstrip(".") for ext in default_music_extensions.split(",") if ext.strip()}
    video_exts = {ext.strip().lstrip(".") for ext in default_extensions_list}
    has_audio, has_video = detect_media_in_path_fn(incoming, audio_exts, video_exts)
    if has_audio and not has_video:
        if confirm_fn("This looks like music. Switch to music mode? [Y/n]", True, None, show_default=False):
            wizard_music_fn(
                source_override=incoming,
                library_override=library,
                log_level=log_level,
                log_format=log_format,
                log_file=log_file,
            )
            return

    media_type = prompt_choice_loop_fn(
        "Media type (movie/tv/both)",
        wizard_media_choices,
        None,
        allow_empty=True,
        error="Enter one of: movie, tv, both.",
        default="movie",
    )

    mode = prompt_choice_loop_fn(
        "Mode (dry-run/apply)",
        wizard_mode_choices,
        None,
        allow_empty=True,
        error="Enter one of: dry-run, apply.",
        default="dry-run",
    )

    copy_mode = True
    prune_empty_dirs = False
    if mode == "apply":
        copy_choice = prompt_choice_loop_fn(
            "Copy or move? (copy/move)",
            wizard_copy_choices,
            None,
            allow_empty=True,
            error="Enter one of: copy, move.",
            default="copy",
        )
        copy_mode = copy_choice == "copy"
        if not copy_mode:
            console.print("Warning: move will remove the original files from the incoming folder.")
            prune_empty_dirs = confirm_fn("Prune empty folders after move? [y/N]", False, None, show_default=False)

    auto_accept = confirm_fn("Auto-accept unambiguous high-confidence matches? [Y/n]", True, None, show_default=False)
    while True:
        min_text = prompt_text_fn("Minimum confidence", str(default_min_confidence), None)
        try:
            min_confidence = float(min_text)
        except ValueError:
            console.print("Enter a number between 0 and 1.")
            continue
        if 0 <= min_confidence <= 1:
            break
        console.print("Enter a number between 0 and 1.")

    use_cache = confirm_fn("Use cache? [Y/n]", True, None, show_default=False)
    clear_cache = False
    if use_cache:
        clear_cache = confirm_fn("Clear cache before running? [y/N]", False, None, show_default=False)

    interactive = confirm_fn("Interactive mode? [Y/n]", True, None, show_default=False)

    command_config = build_command_config_cls(
        incoming=incoming,
        library=library,
        media_type=media_type,
        mode=mode,
        copy_mode=copy_mode,
        extensions=default_extensions_list,
        min_confidence=min_confidence,
        limit=None,
        interactive=interactive,
        print_tree=False,
        show_enrichment=False,
        yes=auto_accept,
        no_cache=not use_cache,
        cache_file=None,
        clear_cache=clear_cache,
        report=None,
        on_conflict="rename",
        prune_empty_dirs=prune_empty_dirs,
        quiet=False,
        prune_ignore=default_prune_ignore,
        allow_risky_enter_accept=False,
        strict_safe=False,
    )
    command = build_command_fn(command_config)
    console.print("Running:")
    console.print(command)

    run_organise_fn(
        organise_options_cls(
            incoming=incoming,
            library=library,
            mode=mode,
            copy_mode=copy_mode,
            extensions=default_extensions,
            min_confidence=min_confidence,
            cache=None,
            report=None,
            yes=auto_accept,
            limit=None,
            print_tree=False,
            interactive_mode=interactive,
            media_type=media_type,
            no_cache=not use_cache,
            clear_cache=clear_cache,
            offline=False,
            on_conflict="rename",
            log_level=log_level,
            log_format=log_format,
            log_file=log_file,
            prune_empty_dirs=prune_empty_dirs,
            prune_ignore=default_prune_ignore,
            quiet=False,
            allow_risky_enter_accept=False,
            strict_safe=False,
        )
    )


def wizard_music(
    *,
    source_override: Path | None,
    library_override: Path | None,
    log_level: str,
    log_format: str,
    log_file: Path | None,
    completion_enabled: bool,
    console: Any,
    wizard_defaults_fn: Any,
    prompt_non_overlapping_paths_fn: Any,
    save_wizard_prefs_fn: Any,
    detect_media_in_path_fn: Any,
    confirm_fn: Any,
    wizard_video_fn: Any,
    prompt_choice_loop_fn: Any,
    prompt_int_fn: Any,
    music_fn: Any,
    default_music_extensions: str,
    default_extensions_list: list[str],
    wizard_mode_choices: set[str],
    wizard_copy_choices: set[str],
    wizard_music_mismatch_choices: set[str],
    wizard_music_plan_output_choices: set[str],
) -> None:
    console.print("This will help you organise music into a Plex-friendly folder layout.")
    if completion_enabled:
        console.print("Tip: run python -m plexify.cli --install-completion to enable shell autocompletion.")

    if source_override or library_override:
        source_default = source_override
        library_default = library_override
    else:
        source_default, library_default = wizard_defaults_fn("music")
    source, library = prompt_non_overlapping_paths_fn(
        label_source="Source",
        label_library="Library",
        source_default=source_default,
        library_default=library_default,
    )
    save_wizard_prefs_fn("music", source, library)

    audio_exts = {ext.strip().lstrip(".") for ext in default_music_extensions.split(",") if ext.strip()}
    video_exts = {ext.strip().lstrip(".") for ext in default_extensions_list}
    has_audio, has_video = detect_media_in_path_fn(source, audio_exts, video_exts)
    if has_video and not has_audio:
        if confirm_fn("This looks like video. Switch to video mode? [Y/n]", True, None, show_default=False):
            wizard_video_fn(log_level=log_level, log_format=log_format, log_file=log_file)
            return

    mode = prompt_choice_loop_fn(
        "Mode (dry-run/apply)",
        wizard_mode_choices,
        None,
        allow_empty=True,
        error="Enter one of: dry-run, apply.",
        default="dry-run",
    )

    copy_mode = False
    cleanup_empty_dirs = False
    cleanup_unknown_files = False
    if mode == "apply":
        copy_choice = prompt_choice_loop_fn(
            "Copy or move? (copy/move)",
            wizard_copy_choices,
            None,
            allow_empty=True,
            error="Enter one of: copy, move.",
            default="move",
        )
        copy_mode = copy_choice == "copy"
        if not copy_mode:
            console.print("Warning: move will remove the original files from the source folder.")
            cleanup_empty_dirs = confirm_fn("Clean up empty folders after move? [y/N]", False, None, show_default=False)
            if cleanup_empty_dirs:
                cleanup_unknown_files = confirm_fn(
                    "Remove unknown leftover files to help prune source folders? [y/N]",
                    False,
                    None,
                    show_default=False,
                )

    verify = confirm_fn("Verify albums with MusicBrainz? [Y/n]", True, None, show_default=False)
    keep_art = confirm_fn("Keep album artwork? [Y/n]", True, None, show_default=False)
    keep_cue = confirm_fn("Keep .cue sidecars? [y/N]", False, None, show_default=False)
    keep_log = confirm_fn("Keep .log sidecars? [y/N]", False, None, show_default=False)
    mismatch_policy = prompt_choice_loop_fn(
        "Track mismatch handling (ask/filename/filename-titles/order)",
        wizard_music_mismatch_choices,
        None,
        allow_empty=True,
        error="Enter one of: ask, filename, filename-titles, order.",
        default="ask",
    )
    plan_output = prompt_choice_loop_fn(
        "Plan output (summary/preview/full)",
        wizard_music_plan_output_choices,
        None,
        allow_empty=True,
        error="Enter one of: summary, preview, full.",
        default="summary",
    )
    verbose_plan = plan_output == "full"
    plan_preview_tracks = 0
    if plan_output == "preview":
        while True:
            plan_preview_tracks = prompt_int_fn("Preview tracks per album", 5, None)
            if plan_preview_tracks > 0:
                break
            console.print("Enter a positive number.")

    music_fn(
        source=source,
        library=library,
        apply=mode == "apply",
        copy=copy_mode,
        extensions=default_music_extensions,
        verify=verify,
        keep_art=keep_art,
        keep_cue=keep_cue,
        keep_log=keep_log,
        offline=False,
        cleanup_empty_dirs=cleanup_empty_dirs,
        cleanup_unknown_files=cleanup_unknown_files,
        verbose_plan=verbose_plan,
        plan_preview_tracks=plan_preview_tracks,
        mismatch_policy=mismatch_policy,
        log_level=log_level,
        log_format=log_format,
        log_file=log_file,
    )
