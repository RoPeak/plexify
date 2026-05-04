from __future__ import annotations

import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Callable, Iterable

from .logging_config import get_logger
from .util import ExecutionResult, MovePlan, ensure_dir, unique_path

logger = get_logger(__name__)

COPY_PROGRESS_INTERVAL_SECONDS = 1.0
COPY_PROGRESS_INTERVAL_BYTES = 64 * 1024 * 1024
COPY_BUFFER_SIZE = 8 * 1024 * 1024


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


def _copy_with_progress(
    source: Path,
    destination: Path,
    *,
    progress_callback: Callable[[int], None] | None = None,
) -> int:
    copied = 0
    last_emit_time = time.monotonic()
    last_emit_bytes = 0
    with source.open("rb") as src, destination.open("wb") as dst:
        while True:
            chunk = src.read(COPY_BUFFER_SIZE)
            if not chunk:
                break
            dst.write(chunk)
            copied += len(chunk)
            now = time.monotonic()
            if (
                progress_callback is not None
                and (
                    now - last_emit_time >= COPY_PROGRESS_INTERVAL_SECONDS
                    or copied - last_emit_bytes >= COPY_PROGRESS_INTERVAL_BYTES
                )
            ):
                progress_callback(copied)
                last_emit_time = now
                last_emit_bytes = copied
    shutil.copystat(source, destination)
    if progress_callback is not None:
        progress_callback(copied)
    return copied


def _copy_to_destination(
    source: Path,
    destination: Path,
    *,
    overwrite: bool,
    progress_callback: Callable[[int], None] | None = None,
) -> int:
    if overwrite:
        tmp_destination = _overwrite_temp_path(destination)
        try:
            copied = _copy_with_progress(source, tmp_destination, progress_callback=progress_callback)
            os.replace(tmp_destination, destination)
            return copied
        except Exception:
            try:
                tmp_destination.unlink(missing_ok=True)
            except OSError:
                logger.warning("overwrite_temp_cleanup_failed", extra={"path": str(tmp_destination)})
            raise
    return _copy_with_progress(source, destination, progress_callback=progress_callback)


def execute_plans(
    plans: Iterable[MovePlan],
    apply: bool,
    copy_mode: bool,
    on_conflict: str = "rename",
    on_progress: Callable[[int, int, MovePlan], None] | None = None,
    on_applied: Callable[[MovePlan], None] | None = None,
    on_plan_started: Callable[[int, int, MovePlan, str], None] | None = None,
    on_plan_event: Callable[[dict[str, object]], None] | None = None,
    cancel_callback: Callable[[], bool] | None = None,
    copy_workers: int = 1,
) -> ExecutionResult:
    plan_list = list(plans)
    copy_workers = max(1, min(int(copy_workers or 1), 4))
    if apply and copy_mode and copy_workers > 1:
        return _execute_copy_plans_parallel(
            plan_list,
            on_conflict=on_conflict,
            on_progress=on_progress,
            on_applied=on_applied,
            on_plan_started=on_plan_started,
            on_plan_event=on_plan_event,
            cancel_callback=cancel_callback,
            copy_workers=copy_workers,
        )

    moved: list[MovePlan] = []
    skipped: list[MovePlan] = []
    errors: list[str] = []
    completed = 0
    total = len(plan_list)
    total_bytes = _total_source_bytes(plan_list)
    completed_bytes = 0

    for plan in plan_list:
        if cancel_callback is not None and cancel_callback():
            if on_plan_event is not None:
                on_plan_event(
                    {
                        "phase": "cancelled",
                        "completed": completed,
                        "total": total,
                        "current_source": plan.source,
                        "current_destination": plan.destination,
                        "operation": "cancelled",
                        "cancel_requested": True,
                        "completed_bytes": completed_bytes,
                        "total_bytes": total_bytes,
                        "parallel_workers": 1,
                        "progress_capability": "byte-copy" if copy_mode else "item",
                        "message": "Organisation cancelled before starting the next file.",
                    }
                )
            break
        operation = "copying" if copy_mode else "moving"
        if not apply:
            operation = "dry-run"
        started_at = _utc_now()
        if on_plan_started:
            on_plan_started(completed, total, plan, operation)
        if on_plan_event is not None:
            on_plan_event(
                _plan_event(
                    phase=operation,
                    completed=completed,
                    total=total,
                    plan=plan,
                    operation=operation,
                    started_at=started_at,
                    conflict_action=on_conflict,
                    current_file_bytes_copied=0,
                    completed_bytes=completed_bytes,
                    total_bytes=total_bytes,
                    parallel_workers=1,
                    progress_capability="byte-copy" if copy_mode else "item",
                    message=f"{operation.replace('-', ' ').title()} {plan.source.name}",
                )
            )
        if not apply:
            skipped.append(plan)
            completed += 1
            if on_progress:
                on_progress(completed, total, plan)
            if on_plan_event is not None:
                on_plan_event(
                    _plan_event(
                        phase="skipped-item",
                        completed=completed,
                        total=total,
                        plan=plan,
                        operation=operation,
                        started_at=started_at,
                        completed_at=_utc_now(),
                        conflict_action=on_conflict,
                        current_file_bytes_copied=0,
                        completed_bytes=completed_bytes,
                        total_bytes=total_bytes,
                        parallel_workers=1,
                        progress_capability="item",
                        message=f"Skipped dry-run item {plan.source.name}",
                    )
                )
            continue
        try:
            destination = plan.destination
            if destination.exists():
                if on_conflict == "skip":
                    skipped.append(plan)
                    completed += 1
                    if on_progress:
                        on_progress(completed, total, plan)
                    if on_plan_event is not None:
                        on_plan_event(
                            _plan_event(
                                phase="skipped-item",
                                completed=completed,
                                total=total,
                                plan=MovePlan(plan.source, destination, plan.mode, plan.media_type, plan.metadata),
                                operation=operation,
                                started_at=started_at,
                                completed_at=_utc_now(),
                                conflict_action="skip",
                                current_file_bytes_copied=0,
                                completed_bytes=completed_bytes,
                                total_bytes=total_bytes,
                                parallel_workers=1,
                                progress_capability="item",
                                message=f"Skipped existing destination {destination.name}",
                            )
                        )
                    continue
                if on_conflict == "overwrite":
                    if destination.is_dir():
                        errors.append(f"{plan.source}: destination is a directory ({destination})")
                        completed += 1
                        if on_progress:
                            on_progress(completed, total, plan)
                        if on_plan_event is not None:
                            on_plan_event(
                                _plan_event(
                                    phase="error-item",
                                    completed=completed,
                                    total=total,
                                    plan=MovePlan(plan.source, destination, plan.mode, plan.media_type, plan.metadata),
                                    operation=operation,
                                    started_at=started_at,
                                    completed_at=_utc_now(),
                                    conflict_action=on_conflict,
                                    error=f"destination is a directory ({destination})",
                                    current_file_bytes_copied=0,
                                    completed_bytes=completed_bytes,
                                    total_bytes=total_bytes,
                                    parallel_workers=1,
                                    progress_capability="item",
                                    message=f"Error applying {plan.source.name}",
                                )
                            )
                        continue
                elif on_conflict == "rename":
                    destination = unique_path(destination)
            ensure_dir(destination.parent)
            planned = MovePlan(plan.source, destination, plan.mode, plan.media_type, plan.metadata)
            if destination.exists() and on_conflict == "overwrite":
                if copy_mode:
                    copied = _copy_to_destination(
                        plan.source,
                        destination,
                        overwrite=True,
                        progress_callback=(
                            lambda copied_bytes, active_plan=planned: _emit_copy_progress(
                                on_plan_event,
                                active_plan,
                                operation=operation,
                                completed=completed,
                                total=total,
                                completed_bytes=completed_bytes,
                                total_bytes=total_bytes,
                                current_file_bytes_copied=copied_bytes,
                                parallel_workers=1,
                                conflict_action=on_conflict,
                                started_at=started_at,
                            )
                        ),
                    )
                else:
                    _replace_destination_atomically(plan.source, destination, remove_source_after=True)
                    copied = _safe_source_size(plan.source)
            elif copy_mode:
                copied = _copy_to_destination(
                    plan.source,
                    destination,
                    overwrite=False,
                    progress_callback=(
                        lambda copied_bytes, active_plan=planned: _emit_copy_progress(
                            on_plan_event,
                            active_plan,
                            operation=operation,
                            completed=completed,
                            total=total,
                            completed_bytes=completed_bytes,
                            total_bytes=total_bytes,
                            current_file_bytes_copied=copied_bytes,
                            parallel_workers=1,
                            conflict_action=on_conflict,
                            started_at=started_at,
                        )
                    ),
                )
            else:
                shutil.move(plan.source, destination)
                copied = _safe_source_size(destination)
            _verify_applied(plan.source, destination, copy_mode=copy_mode)
            completed_bytes += copied if copy_mode else _safe_source_size(destination)
            applied = planned
            moved.append(applied)
            if on_applied is not None:
                on_applied(applied)
            if on_plan_event is not None:
                on_plan_event(
                    _plan_event(
                        phase="completed-item",
                        completed=completed + 1,
                        total=total,
                        plan=applied,
                        operation=operation,
                        started_at=started_at,
                        completed_at=_utc_now(),
                        conflict_action=on_conflict,
                        current_file_bytes_copied=copied,
                        completed_bytes=completed_bytes,
                        total_bytes=total_bytes,
                        parallel_workers=1,
                        progress_capability="byte-copy" if copy_mode else "item",
                        message=f"Completed {applied.source.name}",
                    )
                )
        except (OSError, shutil.Error, ValueError) as exc:
            logger.exception("plan_execution_failed", extra={"source": plan.source, "destination": plan.destination})
            errors.append(f"{plan.source}: {exc}")
            if on_plan_event is not None:
                on_plan_event(
                    _plan_event(
                        phase="error-item",
                        completed=completed + 1,
                        total=total,
                        plan=plan,
                        operation=operation,
                        started_at=started_at,
                        completed_at=_utc_now(),
                        conflict_action=on_conflict,
                        error=str(exc),
                        current_file_bytes_copied=0,
                        completed_bytes=completed_bytes,
                        total_bytes=total_bytes,
                        parallel_workers=1,
                        progress_capability="byte-copy" if copy_mode else "item",
                        message=f"Error applying {plan.source.name}: {exc}",
                    )
                )
        completed += 1
        if on_progress:
            on_progress(completed, total, plan)
    return ExecutionResult(moved=moved, skipped=skipped, errors=errors)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_source_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _total_source_bytes(plans: Iterable[MovePlan]) -> int:
    return sum(_safe_source_size(plan.source) for plan in plans)


def _verify_applied(source: Path, destination: Path, *, copy_mode: bool) -> None:
    if not destination.exists():
        raise OSError(f"destination was not created ({destination})")
    if copy_mode:
        source_size = _safe_source_size(source)
        destination_size = _safe_source_size(destination)
        if source_size != destination_size:
            raise OSError(
                f"copy verification failed for {destination}: expected {source_size} bytes, got {destination_size}"
            )


def _emit_copy_progress(
    on_plan_event: Callable[[dict[str, object]], None] | None,
    plan: MovePlan,
    *,
    operation: str,
    completed: int,
    total: int,
    completed_bytes: int,
    total_bytes: int,
    current_file_bytes_copied: int,
    parallel_workers: int,
    conflict_action: str,
    started_at: str,
) -> None:
    if on_plan_event is None:
        return
    on_plan_event(
        _plan_event(
            phase=operation,
            completed=completed,
            total=total,
            plan=plan,
            operation=operation,
            started_at=started_at,
            conflict_action=conflict_action,
            current_file_bytes_copied=current_file_bytes_copied,
            completed_bytes=completed_bytes + current_file_bytes_copied,
            total_bytes=total_bytes,
            active_files=1,
            parallel_workers=parallel_workers,
            progress_capability="byte-copy",
            message=f"{operation.replace('-', ' ').title()} {plan.source.name}",
        )
    )


def _execute_copy_plans_parallel(
    plan_list: list[MovePlan],
    *,
    on_conflict: str,
    on_progress: Callable[[int, int, MovePlan], None] | None,
    on_applied: Callable[[MovePlan], None] | None,
    on_plan_started: Callable[[int, int, MovePlan, str], None] | None,
    on_plan_event: Callable[[dict[str, object]], None] | None,
    cancel_callback: Callable[[], bool] | None,
    copy_workers: int,
) -> ExecutionResult:
    plan_list = _reserve_parallel_destinations(plan_list, on_conflict=on_conflict)
    moved: list[MovePlan] = []
    skipped: list[MovePlan] = []
    errors: list[str] = []
    total = len(plan_list)
    total_bytes = _total_source_bytes(plan_list)
    completed = 0
    completed_bytes = 0
    active_files = 0
    state_lock = Lock()
    callback_lock = Lock()

    def _emit(payload: dict[str, object]) -> None:
        if on_plan_event is None:
            return
        with callback_lock:
            on_plan_event(payload)

    def _copy_one(index: int, plan: MovePlan) -> tuple[str, MovePlan, int, str | None]:
        nonlocal active_files
        operation = "copying"
        started_at = _utc_now()
        destination = plan.destination
        if on_plan_started is not None:
            with callback_lock:
                on_plan_started(index, total, plan, operation)
        try:
            if destination.exists():
                if on_conflict == "skip":
                    _emit(
                        _plan_event(
                            phase="skipped-item",
                            completed=index,
                            total=total,
                            plan=MovePlan(plan.source, destination, plan.mode, plan.media_type, plan.metadata),
                            operation=operation,
                            started_at=started_at,
                            completed_at=_utc_now(),
                            conflict_action="skip",
                            current_file_bytes_copied=0,
                            completed_bytes=completed_bytes,
                            total_bytes=total_bytes,
                            active_files=active_files,
                            parallel_workers=copy_workers,
                            progress_capability="byte-copy",
                            message=f"Skipped existing destination {destination.name}",
                        )
                    )
                    return "skipped", MovePlan(plan.source, destination, plan.mode, plan.media_type, plan.metadata), 0, None
                if on_conflict == "overwrite" and destination.is_dir():
                    return "error", plan, 0, f"destination is a directory ({destination})"
                if on_conflict == "rename":
                    destination = unique_path(destination)
            ensure_dir(destination.parent)
            applied = MovePlan(plan.source, destination, plan.mode, plan.media_type, plan.metadata)
            with state_lock:
                active_files += 1
                local_active = active_files
                base_completed_bytes = completed_bytes
            _emit(
                _plan_event(
                    phase=operation,
                    completed=index,
                    total=total,
                    plan=applied,
                    operation=operation,
                    started_at=started_at,
                    conflict_action=on_conflict,
                    current_file_bytes_copied=0,
                    completed_bytes=base_completed_bytes,
                    total_bytes=total_bytes,
                    active_files=local_active,
                    parallel_workers=copy_workers,
                    progress_capability="byte-copy",
                    message=f"Copying {plan.source.name}",
                )
            )

            def _progress(copied_bytes: int) -> None:
                with state_lock:
                    snapshot_completed_bytes = completed_bytes
                    snapshot_active = active_files
                _emit(
                    _plan_event(
                        phase=operation,
                        completed=index,
                        total=total,
                        plan=applied,
                        operation=operation,
                        started_at=started_at,
                        conflict_action=on_conflict,
                        current_file_bytes_copied=copied_bytes,
                        completed_bytes=snapshot_completed_bytes + copied_bytes,
                        total_bytes=total_bytes,
                        active_files=snapshot_active,
                        parallel_workers=copy_workers,
                        progress_capability="byte-copy",
                        message=f"Copying {plan.source.name}",
                    )
                )

            copied = _copy_to_destination(
                plan.source,
                destination,
                overwrite=destination.exists() and on_conflict == "overwrite",
                progress_callback=_progress,
            )
            _verify_applied(plan.source, destination, copy_mode=True)
            return "moved", applied, copied, None
        except (OSError, shutil.Error, ValueError) as exc:
            logger.exception("plan_execution_failed", extra={"source": plan.source, "destination": plan.destination})
            return "error", plan, 0, str(exc)
        finally:
            with state_lock:
                active_files = max(0, active_files - 1)

    with ThreadPoolExecutor(max_workers=copy_workers) as executor:
        plan_iter = iter(enumerate(plan_list))
        futures = set()

        def _submit_next() -> bool:
            try:
                index, plan = next(plan_iter)
            except StopIteration:
                return False
            if cancel_callback is not None and cancel_callback():
                _emit(
                    {
                        "phase": "cancelled",
                        "completed": completed,
                        "total": total,
                        "current_source": plan.source,
                        "current_destination": plan.destination,
                        "operation": "cancelled",
                        "cancel_requested": True,
                        "completed_bytes": completed_bytes,
                        "total_bytes": total_bytes,
                        "active_files": active_files,
                        "parallel_workers": copy_workers,
                        "progress_capability": "byte-copy",
                        "message": "Organisation cancelled before starting more files. Active copies will finish.",
                    }
                )
                return False
            futures.add(executor.submit(_copy_one, index, plan))
            return True

        for _ in range(copy_workers):
            if not _submit_next():
                break

        while futures:
            done_future = next(as_completed(futures))
            futures.remove(done_future)
            status, plan, copied, error = done_future.result()
            completed += 1
            if status == "moved":
                completed_bytes += copied
                moved.append(plan)
                if on_applied is not None:
                    with callback_lock:
                        on_applied(plan)
                _emit(
                    _plan_event(
                        phase="completed-item",
                        completed=completed,
                        total=total,
                        plan=plan,
                        operation="copying",
                        started_at=_utc_now(),
                        completed_at=_utc_now(),
                        conflict_action=on_conflict,
                        current_file_bytes_copied=copied,
                        completed_bytes=completed_bytes,
                        total_bytes=total_bytes,
                        active_files=active_files,
                        parallel_workers=copy_workers,
                        progress_capability="byte-copy",
                        message=f"Completed {plan.source.name}",
                    )
                )
            elif status == "skipped":
                skipped.append(plan)
            else:
                errors.append(f"{plan.source}: {error}")
                _emit(
                    _plan_event(
                        phase="error-item",
                        completed=completed,
                        total=total,
                        plan=plan,
                        operation="copying",
                        started_at=_utc_now(),
                        completed_at=_utc_now(),
                        conflict_action=on_conflict,
                        error=error,
                        current_file_bytes_copied=0,
                        completed_bytes=completed_bytes,
                        total_bytes=total_bytes,
                        active_files=active_files,
                        parallel_workers=copy_workers,
                        progress_capability="byte-copy",
                        message=f"Error applying {plan.source.name}: {error}",
                    )
                )
            if on_progress is not None:
                with callback_lock:
                    on_progress(completed, total, plan)
            _submit_next()
    return ExecutionResult(moved=moved, skipped=skipped, errors=errors)


def _reserve_parallel_destinations(plan_list: list[MovePlan], *, on_conflict: str) -> list[MovePlan]:
    if on_conflict != "rename":
        return plan_list
    reserved: set[Path] = set()
    resolved: list[MovePlan] = []
    for plan in plan_list:
        destination = plan.destination
        counter = 2
        while destination in reserved or destination.exists():
            destination = plan.destination.with_name(f"{plan.destination.stem} ({counter}){plan.destination.suffix}")
            counter += 1
        reserved.add(destination)
        resolved.append(MovePlan(plan.source, destination, plan.mode, plan.media_type, plan.metadata))
    return resolved


def _plan_event(
    *,
    phase: str,
    completed: int,
    total: int,
    plan: MovePlan,
    operation: str,
    started_at: str,
    completed_at: str | None = None,
    conflict_action: str | None = None,
    error: str | None = None,
    current_file_bytes_copied: int | None = None,
    completed_bytes: int | None = None,
    total_bytes: int | None = None,
    active_files: int | None = None,
    parallel_workers: int | None = None,
    progress_capability: str | None = None,
    message: str,
) -> dict[str, object]:
    source_size = _safe_source_size(plan.source)
    payload: dict[str, object] = {
        "phase": phase,
        "completed": completed,
        "total": total,
        "current_source": plan.source,
        "current_destination": plan.destination,
        "operation": operation,
        "source_size_bytes": source_size,
        "started_at": started_at,
        "conflict_action": conflict_action,
        "message": message,
    }
    if current_file_bytes_copied is not None:
        payload["current_file_bytes_copied"] = current_file_bytes_copied
    if completed_bytes is not None:
        payload["completed_bytes"] = completed_bytes
        payload["bytes_copied"] = completed_bytes
    if total_bytes is not None:
        payload["total_bytes"] = total_bytes
    if active_files is not None:
        payload["active_files"] = active_files
    if parallel_workers is not None:
        payload["parallel_workers"] = parallel_workers
    if progress_capability is not None:
        payload["progress_capability"] = progress_capability
    if completed_at is not None:
        payload["completed_at"] = completed_at
        payload["bytes_copied"] = completed_bytes if completed_bytes is not None else source_size
        payload["last_applied_source"] = plan.source
    if error is not None:
        payload["error"] = error
    return payload
