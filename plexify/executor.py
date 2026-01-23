from __future__ import annotations

import shutil
from pathlib import Path
from typing import Iterable

from .util import ExecutionResult, MovePlan, ensure_dir, unique_path


def execute_plans(plans: Iterable[MovePlan], apply: bool, copy_mode: bool) -> ExecutionResult:
    moved: list[MovePlan] = []
    skipped: list[MovePlan] = []
    errors: list[str] = []

    for plan in plans:
        if not apply:
            skipped.append(plan)
            continue
        try:
            destination = unique_path(plan.destination)
            ensure_dir(destination.parent)
            if copy_mode:
                shutil.copy2(plan.source, destination)
            else:
                shutil.move(plan.source, destination)
            moved.append(MovePlan(plan.source, destination, plan.mode, plan.media_type, plan.metadata))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{plan.source}: {exc}")
    return ExecutionResult(moved=moved, skipped=skipped, errors=errors)
