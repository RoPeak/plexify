from pathlib import Path

from plexify.report import write_report
from plexify.undo import undo_report
from plexify.util import MovePlan


def test_undo_move_restores_file(tmp_path: Path):
    src = tmp_path / "source.mkv"
    dest = tmp_path / "dest.mkv"
    dest.write_text("data")
    report = tmp_path / "report.json"
    plan = MovePlan(source=src, destination=dest, mode="apply", media_type="movie", metadata={})
    write_report(report, [plan], mode="apply", copy_mode=False)
    errors = undo_report(report)
    assert not errors
    assert src.exists()


def test_undo_copy_removes_destination(tmp_path: Path):
    src = tmp_path / "source.mkv"
    dest = tmp_path / "dest.mkv"
    src.write_text("data")
    dest.write_text("data")
    report = tmp_path / "report.json"
    plan = MovePlan(source=src, destination=dest, mode="apply", media_type="movie", metadata={})
    write_report(report, [plan], mode="apply", copy_mode=True)
    errors = undo_report(report)
    assert not errors
    assert not dest.exists()


def test_undo_move_creates_missing_source_parent(tmp_path: Path):
    src = tmp_path / "missing" / "source.mkv"
    dest = tmp_path / "dest.mkv"
    dest.write_text("data")
    report = tmp_path / "report.json"
    plan = MovePlan(source=src, destination=dest, mode="apply", media_type="movie", metadata={})
    write_report(report, [plan], mode="apply", copy_mode=False)
    errors = undo_report(report)
    assert not errors
    assert src.exists()


def test_undo_move_errors_when_source_exists(tmp_path: Path):
    src = tmp_path / "source.mkv"
    dest = tmp_path / "dest.mkv"
    src.write_text("existing")
    dest.write_text("data")
    report = tmp_path / "report.json"
    plan = MovePlan(source=src, destination=dest, mode="apply", media_type="movie", metadata={})
    write_report(report, [plan], mode="apply", copy_mode=False)
    errors = undo_report(report)
    assert errors
    assert dest.exists()
