from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable, Iterable

from .util import ExecutionResult, MovePlan, ensure_dir, unique_path


def execute_plans(
    plans: Iterable[MovePlan],
    apply: bool,
    copy_mode: bool,
    on_conflict: str = "rename",
    on_progress: Callable[[int, int, MovePlan], None] | None = None,
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
                    destination.unlink()
                elif on_conflict == "rename":
                    destination = unique_path(destination)
            ensure_dir(destination.parent)
            if copy_mode:
                shutil.copy2(plan.source, destination)
            else:
                shutil.move(plan.source, destination)
            moved.append(MovePlan(plan.source, destination, plan.mode, plan.media_type, plan.metadata))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{plan.source}: {exc}")
        completed += 1
        if on_progress:
            on_progress(completed, total, plan)
    return ExecutionResult(moved=moved, skipped=skipped, errors=errors)
