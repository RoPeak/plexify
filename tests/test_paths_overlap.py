from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from plexify import cli
from plexify.paths import PathOverlapError, ensure_non_overlapping_paths, validate_non_overlapping


def test_overlap_same_path(tmp_path: Path) -> None:
    with pytest.raises(PathOverlapError):
        ensure_non_overlapping_paths(tmp_path, tmp_path)
    ok, reason, suggestion = validate_non_overlapping(tmp_path, tmp_path)
    assert ok is False
    assert "same folder" in reason.lower() or "same" in reason.lower()
    assert suggestion is not None


def test_overlap_source_inside_library(tmp_path: Path) -> None:
    library = tmp_path / "Library"
    source = library / "Incoming"
    with pytest.raises(PathOverlapError):
        ensure_non_overlapping_paths(source, library)
    ok, reason, suggestion = validate_non_overlapping(source, library)
    assert ok is False
    assert "inside" in reason.lower()
    assert suggestion is not None


def test_overlap_library_inside_source(tmp_path: Path) -> None:
    source = tmp_path / "Incoming"
    library = source / "Organised"
    with pytest.raises(PathOverlapError):
        ensure_non_overlapping_paths(source, library)
    ok, reason, suggestion = validate_non_overlapping(source, library)
    assert ok is False
    assert "inside" in reason.lower()
    assert suggestion is not None


def test_overlap_case_insensitive() -> None:
    left = Path("C:\\Temp\\Incoming")
    right = Path("c:\\temp\\incoming")
    with pytest.raises(PathOverlapError):
        ensure_non_overlapping_paths(left, right)
    ok, reason, suggestion = validate_non_overlapping(left, right)
    assert ok is False
    assert reason


def test_non_overlapping_paths_ok(tmp_path: Path) -> None:
    source = tmp_path / "Incoming"
    library = tmp_path / "Library"
    ensure_non_overlapping_paths(source, library)
    ok, reason, suggestion = validate_non_overlapping(source, library)
    assert ok is True
    assert reason == ""
    assert suggestion is None


def test_music_command_rejects_overlap(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        ["music", "--source", str(tmp_path), "--library", str(tmp_path), "--dry-run"],
    )
    assert result.exit_code == 2
    assert "same folder" in result.output.lower()


def test_organise_command_rejects_overlap(tmp_path: Path) -> None:
    runner = CliRunner()
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    result = runner.invoke(
        cli.app,
        ["organise", "--incoming", str(incoming), "--library", str(incoming), "--mode", "dry-run"],
    )
    assert result.exit_code == 2
    assert "same folder" in result.output.lower() or "inside" in result.output.lower()


def test_prompt_path_fallback_message(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "_path_prompt_fallback_tip_shown", False)
    monkeypatch.setattr(cli, "_path_prompt_tip_shown", False)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(cli.Prompt, "ask", lambda *_args, **_kwargs: "C:\\Temp")
    cli._prompt_path("Test path", "C:\\Temp", directories_only=True)
    output = capsys.readouterr().out
    assert "install prompt_toolkit" in output.lower()


def test_prompt_path_uses_prompt_toolkit(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "_path_prompt_fallback_tip_shown", False)
    monkeypatch.setattr(cli, "_path_prompt_tip_shown", False)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.Prompt, "ask", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("fallback used")))
    import prompt_toolkit.shortcuts as pt_shortcuts

    def _fake_prompt(*_args, **_kwargs):
        return "\"C:\\Music\""

    monkeypatch.setattr(pt_shortcuts, "prompt", _fake_prompt)
    result = cli._prompt_path("Test path", "C:\\Music", directories_only=True)
    assert result == "C:\\Music"
    output = capsys.readouterr().out
    assert "tab autocompletes paths" in output.lower()
