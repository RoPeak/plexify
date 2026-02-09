from pathlib import Path

from typer.testing import CliRunner

from plexify import cli


def test_undo_report_outside_default_location_requires_library(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    report.write_text('{"copy": false, "operations": []}', encoding="utf-8")
    runner = CliRunner()

    result = runner.invoke(cli.app, ["undo", "--report", str(report)])

    assert result.exit_code == 2
    assert "Provide --library" in result.output


def test_undo_infers_library_from_default_report_path(tmp_path: Path, monkeypatch) -> None:
    library = tmp_path / "library"
    report = library / ".plexify" / "reports" / "run.json"
    report.parent.mkdir(parents=True)
    report.write_text('{"copy": false, "operations": []}', encoding="utf-8")
    captured: dict[str, Path | None] = {"library_root": None}

    def _fake_undo(path: Path, library_root: Path | None = None) -> list[str]:
        captured["library_root"] = library_root
        return []

    monkeypatch.setattr(cli, "undo_report", _fake_undo)
    runner = CliRunner()

    result = runner.invoke(cli.app, ["undo", "--report", str(report)])

    assert result.exit_code == 0
    assert captured["library_root"] == library


def test_undo_invalid_report_returns_usage_error(tmp_path: Path) -> None:
    library = tmp_path / "library"
    library.mkdir()
    report = library / ".plexify" / "reports" / "broken.json"
    report.parent.mkdir(parents=True)
    report.write_text("{not-json", encoding="utf-8")
    runner = CliRunner()

    result = runner.invoke(cli.app, ["undo", "--report", str(report)])

    assert result.exit_code == 2
    assert "Invalid report:" in result.output
