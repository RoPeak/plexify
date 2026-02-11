from __future__ import annotations

import os
import shlex
from pathlib import Path


def quote_cli_arg(value: str) -> str:
    # PowerShell single-quoted strings escape embedded apostrophes by doubling.
    if os.name == "nt":
        return "'" + value.replace("'", "''") + "'"
    return shlex.quote(value)


def build_organise_command(
    *,
    incoming: Path,
    library: Path,
    media_type: str,
    mode: str,
    copy_mode: bool,
    default_extensions: list[str],
    extensions: list[str],
    default_min_confidence: float,
    min_confidence: float,
    limit: int | None,
    interactive: bool,
    print_tree: bool,
    yes: bool,
    no_cache: bool,
    cache_file: Path | None,
    clear_cache: bool,
    report: Path | None,
    on_conflict: str,
    prune_empty_dirs: bool,
    quiet: bool,
    prune_ignore: str | None,
) -> str:
    parts = [
        "python -m plexify.cli organise",
        f"--incoming {quote_cli_arg(str(incoming))}",
        f"--library {quote_cli_arg(str(library))}",
    ]
    if mode != "dry-run":
        parts.append(f"--mode {mode}")
    if mode == "apply" and not copy_mode:
        parts.append("--move")
    if print_tree:
        parts.append("--print-tree")
    if extensions != default_extensions:
        parts.append(f"--extensions {quote_cli_arg(','.join(extensions))}")
    if min_confidence != default_min_confidence:
        parts.append(f"--min-confidence {min_confidence}")
    if limit is not None:
        parts.append(f"--limit {limit}")
    if media_type != "auto":
        parts.append(f"--media-type {media_type}")
    if yes:
        parts.append("--yes")
    if no_cache:
        parts.append("--no-cache")
    if cache_file is not None:
        parts.append(f"--cache {quote_cli_arg(str(cache_file))}")
    if report is not None:
        parts.append(f"--report {quote_cli_arg(str(report))}")
    if clear_cache:
        parts.append("--clear-cache")
    if on_conflict != "rename":
        parts.append(f"--on-conflict {on_conflict}")
    if prune_empty_dirs:
        parts.append("--prune-empty-dirs")
    if prune_ignore and prune_ignore != "Thumbs.db,desktop.ini,.DS_Store":
        parts.append(f"--prune-ignore {quote_cli_arg(prune_ignore)}")
    if quiet:
        parts.append("--quiet")
    if not interactive:
        parts.append("--no-interactive")
    return " ".join(parts)
