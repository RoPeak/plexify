from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .paths import check_non_overlapping_paths


@dataclass(frozen=True)
class PreflightResult:
    incoming: Path
    library: Path
    same_filesystem: bool


class PreflightError(ValueError):
    pass


def validate_paths(
    incoming: Path,
    library: Path,
    *,
    apply: bool,
    require_same_filesystem: bool = False,
    platform: str = "auto",
) -> PreflightResult:
    try:
        source = incoming.expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise PreflightError(f"Incoming path does not exist or cannot be resolved: {incoming}\nNo changes made.") from exc
    try:
        destination = library.expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise PreflightError(
            f"Configured production library does not exist or cannot be resolved: {library}\nNo changes made."
        ) from exc
    if not source.is_dir():
        raise PreflightError(f"Incoming path is not a directory: {source}\nNo changes made.")
    if not destination.is_dir():
        raise PreflightError(f"Configured production library is not a directory: {destination}\nNo changes made.")
    if not os.access(source, os.R_OK | os.X_OK):
        raise PreflightError(f"Incoming directory is not readable: {source}\nNo changes made.")
    if apply and not os.access(destination, os.W_OK | os.X_OK):
        raise PreflightError(f"Production library is not writable: {destination}\nNo changes made.")
    issue = check_non_overlapping_paths(source, destination, platform=platform)
    if issue is not None:
        raise PreflightError(f"{issue.reason}\nNo changes made.")
    try:
        same_filesystem = source.stat().st_dev == destination.stat().st_dev
    except OSError as exc:
        raise PreflightError(f"Cannot inspect filesystem for configured paths: {exc}\nNo changes made.") from exc
    if require_same_filesystem and not same_filesystem:
        raise PreflightError(
            "Incoming and production library paths are on different filesystems, but the configuration requires "
            "the same filesystem.\nNo changes made."
        )
    return PreflightResult(source, destination, same_filesystem)
