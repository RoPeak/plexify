from __future__ import annotations

from pathlib import Path

import pytest

from plexify import executor as executor_module
from plexify.executor import execute_plans
from plexify.report import read_report
from plexify.util import MovePlan


def _plan(source: Path, destination: Path) -> MovePlan:
    return MovePlan(source=source, destination=destination, mode="apply", media_type="movie", metadata={})


def test_execute_plans_emits_rich_plan_events(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"data")
    destination = tmp_path / "library" / "Movie.mp4"
    events: list[dict[str, object]] = []

    result = execute_plans(
        [_plan(source, destination)],
        apply=True,
        copy_mode=True,
        on_plan_event=events.append,
    )

    assert result.moved
    assert any(event["phase"] == "copying" for event in events)
    completed = next(event for event in events if event["phase"] == "completed-item")
    assert completed["source_size_bytes"] == 4
    assert completed["bytes_copied"] == 4
    assert completed["current_destination"] == destination


def test_execute_plans_emits_chunked_copy_progress(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(executor_module, "COPY_BUFFER_SIZE", 2)
    monkeypatch.setattr(executor_module, "COPY_PROGRESS_INTERVAL_BYTES", 2)
    monkeypatch.setattr(executor_module, "COPY_PROGRESS_INTERVAL_SECONDS", 999)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"abcdef")
    destination = tmp_path / "library" / "Movie.mp4"
    events: list[dict[str, object]] = []

    execute_plans(
        [_plan(source, destination)],
        apply=True,
        copy_mode=True,
        on_plan_event=events.append,
    )

    byte_updates = [
        event["current_file_bytes_copied"]
        for event in events
        if event["phase"] == "copying" and event.get("current_file_bytes_copied")
    ]
    assert byte_updates == sorted(byte_updates)
    assert byte_updates[-1] == 6
    assert any(event.get("total_bytes") == 6 for event in events)


def test_execute_plans_reports_verification_error_without_successful_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"abcdef")
    destination = tmp_path / "library" / "Movie.mp4"
    applied: list[object] = []

    def _short_copy(source: Path, destination: Path, *, overwrite: bool, progress_callback=None) -> int:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"abc")
        return 3

    monkeypatch.setattr(executor_module, "_copy_to_destination", _short_copy)

    result = execute_plans(
        [_plan(source, destination)],
        apply=True,
        copy_mode=True,
        on_applied=applied.append,
    )

    assert result.errors
    assert not applied


def test_execute_plans_cancels_between_file_operations(tmp_path: Path) -> None:
    first = tmp_path / "first.mp4"
    second = tmp_path / "second.mp4"
    first.write_bytes(b"1")
    second.write_bytes(b"2")
    first_dest = tmp_path / "library" / "First.mp4"
    second_dest = tmp_path / "library" / "Second.mp4"
    events: list[dict[str, object]] = []
    calls = 0

    def _cancel_after_first() -> bool:
        nonlocal calls
        calls += 1
        return calls > 1

    result = execute_plans(
        [_plan(first, first_dest), _plan(second, second_dest)],
        apply=True,
        copy_mode=True,
        on_plan_event=events.append,
        cancel_callback=_cancel_after_first,
    )

    assert len(result.moved) == 1
    assert first_dest.exists()
    assert not second_dest.exists()
    assert events[-1]["phase"] == "cancelled"
    assert events[-1]["cancel_requested"] is True


def test_execute_plans_parallel_copy_finishes_files_and_reports_workers(tmp_path: Path) -> None:
    plans = []
    for index in range(3):
        source = tmp_path / f"source-{index}.mp4"
        source.write_bytes(f"data-{index}".encode())
        plans.append(_plan(source, tmp_path / "library" / f"Movie-{index}.mp4"))
    events: list[dict[str, object]] = []

    result = execute_plans(
        plans,
        apply=True,
        copy_mode=True,
        copy_workers=2,
        on_plan_event=events.append,
    )

    assert len(result.moved) == 3
    assert all(plan.destination.exists() for plan in plans)
    assert any(event.get("parallel_workers") == 2 for event in events)
    assert any(event.get("progress_capability") == "byte-copy" for event in events)


def test_execute_plans_parallel_cancel_starts_no_more_files(tmp_path: Path) -> None:
    plans = []
    for index in range(3):
        source = tmp_path / f"source-{index}.mp4"
        source.write_bytes(f"data-{index}".encode())
        plans.append(_plan(source, tmp_path / "library" / f"Movie-{index}.mp4"))
    calls = 0

    def _cancel_after_initial_workers() -> bool:
        nonlocal calls
        calls += 1
        return calls > 2

    result = execute_plans(
        plans,
        apply=True,
        copy_mode=True,
        copy_workers=2,
        cancel_callback=_cancel_after_initial_workers,
    )

    assert len(result.moved) == 2
    assert not plans[2].destination.exists()


def test_apply_with_streamed_report_finalizes_after_cancel(tmp_path: Path) -> None:
    pytest.importorskip("guessit")
    from plexify.ui_services import apply_with_streamed_report

    first = tmp_path / "first.mp4"
    second = tmp_path / "second.mp4"
    first.write_bytes(b"1")
    second.write_bytes(b"2")
    report_path = tmp_path / "report.json"
    events: list[dict[str, object]] = []
    calls = 0

    def _cancel_after_first() -> bool:
        nonlocal calls
        calls += 1
        return calls > 1

    result = apply_with_streamed_report(
        [
            _plan(first, tmp_path / "library" / "First.mp4"),
            _plan(second, tmp_path / "library" / "Second.mp4"),
        ],
        copy_mode=True,
        on_conflict="rename",
        report_path=report_path,
        progress_callback=events.append,
        cancel_callback=_cancel_after_first,
        copy_workers=1,
    )

    payload = read_report(report_path)
    assert len(result.moved) == 1
    assert payload["mode"] == "apply"
    assert len(payload["plans"]) == 1
    assert any(event["phase"] == "cancelled" for event in events)
    assert events[-1]["phase"] == "done"
    assert events[-1]["completed"] == 1
