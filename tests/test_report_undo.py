from pathlib import Path

from plexify.report import open_report_stream, read_report, write_report
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


def test_read_report_accepts_jsonl_stream_format(tmp_path: Path) -> None:
    report = tmp_path / "stream.json"
    plan = MovePlan(
        source=tmp_path / "incoming.mkv",
        destination=tmp_path / "library" / "incoming.mkv",
        mode="apply",
        media_type="movie",
        metadata={"title": "Example"},
    )
    stream = open_report_stream(report, mode="apply", copy_mode=False)
    stream.append(plan)
    stream.finalize()
    stream.close()

    payload = read_report(report)

    assert payload["mode"] == "apply"
    assert payload["copy"] is False
    assert len(payload["operations"]) == 1
    assert payload["operations"][0]["destination"] == str(plan.destination)


def test_report_writes_absolute_paths_for_relative_plan(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    plan = MovePlan(
        source=Path("incoming.mkv"),
        destination=Path("library") / "incoming.mkv",
        mode="apply",
        media_type="movie",
        metadata={},
    )
    write_report(report, [plan], mode="apply", copy_mode=False)

    payload = read_report(report)
    operation = payload["operations"][0]
    assert Path(operation["source"]).is_absolute()
    assert Path(operation["destination"]).is_absolute()
    assert operation["source"] == str(plan.source.resolve(strict=False))
    assert operation["destination"] == str(plan.destination.resolve(strict=False))


def test_undo_from_jsonl_report(tmp_path: Path) -> None:
    report = tmp_path / "stream.json"
    src = tmp_path / "source.mkv"
    dest = tmp_path / "dest.mkv"
    dest.write_text("data", encoding="utf-8")
    stream = open_report_stream(report, mode="apply", copy_mode=False)
    stream.append(MovePlan(source=src, destination=dest, mode="apply", media_type="movie", metadata={}))
    stream.finalize()
    stream.close()

    errors = undo_report(report)

    assert not errors
    assert src.exists()
