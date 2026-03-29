from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Callable, Iterable

from .logging_config import get_logger
from .util import ExecutionResult, MovePlan, ensure_dir, unique_path

logger = get_logger(__name__)


def _overwrite_temp_path(destination: Path) -> Path:
    return destination.with_name(f"{destination.name}.plexify.tmp")


def _replace_destination_atomically(source: Path, destination: Path, *, remove_source_after: bool) -> None:
    tmp_destination = _overwrite_temp_path(destination)
    shutil.copy2(source, tmp_destination)
    try:
        os.replace(tmp_destination, destination)
    except Exception:
        try:
            tmp_destination.unlink(missing_ok=True)
        except OSError:
            logger.warning("overwrite_temp_cleanup_failed", extra={"path": str(tmp_destination)})
        raise
    if remove_source_after:
        source.unlink()


def execute_plans(
    plans: Iterable[MovePlan],
    apply: bool,
    copy_mode: bool,
    on_conflict: str = "rename",
    on_progress: Callable[[int, int, MovePlan], None] | None = None,
    on_applied: Callable[[MovePlan], None] | None = None,
) -> ExecutionResult:
    plan_list = list(plans)
    moved: list[MovePlan] = []
    skipped: list[MovePlan] = []
    errors: list[str] = []
    completed = 0
    total = len(plan_list)

    for plan in plan_list:
        if not apply:
            skipped.append(plan)
            completed += 1
            if on_progress:
                on_progress(completed, total, plan)
            continue
        try:
            destination = plan.destination
            if destination.exists():
                if on_conflict == "skip":
                    skipped.append(plan)
                    completed += 1
                    if on_progress:
                        on_progress(completed, total, plan)
                    continue
                if on_conflict == "overwrite":
                    if destination.is_dir():
                        errors.append(f"{plan.source}: destination is a directory ({destination})")
                        completed += 1
                        if on_progress:
                            on_progress(completed, total, plan)
                        continue
                elif on_conflict == "rename":
                    destination = unique_path(destination)
            ensure_dir(destination.parent)
            if destination.exists() and on_conflict == "overwrite":
                if copy_mode:
                    shutil.copy2(plan.source, destination)
                else:
                    _replace_destination_atomically(plan.source, destination, remove_source_after=True)
            elif copy_mode:
                shutil.copy2(plan.source, destination)
            else:
                shutil.move(plan.source, destination)
            applied = MovePlan(plan.source, destination, plan.mode, plan.media_type, plan.metadata)
            moved.append(applied)
            if on_applied is not None:
                on_applied(applied)
        except (OSError, shutil.Error, ValueError) as exc:
            logger.exception("plan_execution_failed", extra={"source": plan.source, "destination": plan.destination})
            errors.append(f"{plan.source}: {exc}")
        completed += 1
        if on_progress:
            on_progress(completed, total, plan)
    return ExecutionResult(moved=moved, skipped=skipped, errors=errors)
