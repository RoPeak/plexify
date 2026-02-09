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


def test_undo_blocks_paths_outside_library_root(tmp_path: Path):
    library = tmp_path / "library"
    library.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    src = outside / "source.mkv"
    dest = outside / "dest.mkv"
    dest.write_text("data")
    report = tmp_path / "report.json"
    plan = MovePlan(source=src, destination=dest, mode="apply", media_type="movie", metadata={})
    write_report(report, [plan], mode="apply", copy_mode=False)

    errors = undo_report(report, library_root=library)

    assert errors
    assert "outside library root" in errors[0]
    assert dest.exists()


def test_undo_allows_move_source_outside_library_root_when_destination_inside(tmp_path: Path):
    library = tmp_path / "library"
    library.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    src = outside / "source.mkv"
    dest = library / "Movies" / "source.mkv"
    dest.parent.mkdir(parents=True)
    dest.write_text("data")
    report = tmp_path / "report.json"
    plan = MovePlan(source=src, destination=dest, mode="apply", media_type="movie", metadata={})
    write_report(report, [plan], mode="apply", copy_mode=False)

    errors = undo_report(report, library_root=library)

    assert not errors
    assert src.exists()
