from pathlib import Path

from plexify import cli
from plexify import executor
from plexify.executor import execute_plans
from plexify.util import MovePlan


def test_conflict_rename_creates_unique_destination(tmp_path: Path) -> None:
    src = tmp_path / "source.mkv"
    dest = tmp_path / "dest.mkv"
    src.write_text("data", encoding="utf-8")
    dest.write_text("old", encoding="utf-8")
    plan = MovePlan(source=src, destination=dest, mode="apply", media_type="movie", metadata={})
    result = execute_plans([plan], apply=True, copy_mode=True, on_conflict="rename")
    assert result.errors == []
    assert result.moved
    assert result.moved[0].destination != dest
    assert result.moved[0].destination.exists()
    assert dest.exists()


def test_conflict_skip_leaves_destination(tmp_path: Path) -> None:
    src = tmp_path / "source.mkv"
    dest = tmp_path / "dest.mkv"
    src.write_text("data", encoding="utf-8")
    dest.write_text("old", encoding="utf-8")
    plan = MovePlan(source=src, destination=dest, mode="apply", media_type="movie", metadata={})
    result = execute_plans([plan], apply=True, copy_mode=True, on_conflict="skip")
    assert result.moved == []
    assert result.skipped == [plan]
    assert dest.exists()


def test_conflict_overwrite_replaces_destination(tmp_path: Path) -> None:
    src = tmp_path / "source.mkv"
    dest = tmp_path / "dest.mkv"
    src.write_text("data", encoding="utf-8")
    dest.write_text("old", encoding="utf-8")
    plan = MovePlan(source=src, destination=dest, mode="apply", media_type="movie", metadata={})
    result = execute_plans([plan], apply=True, copy_mode=True, on_conflict="overwrite")
    assert result.errors == []
    assert result.moved
    assert dest.read_text(encoding="utf-8") == "data"


def test_conflict_overwrite_preserves_destination_when_temp_copy_fails(monkeypatch, tmp_path: Path) -> None:
    src = tmp_path / "source.mkv"
    dest = tmp_path / "dest.mkv"
    src.write_text("data", encoding="utf-8")
    dest.write_text("old", encoding="utf-8")
    plan = MovePlan(source=src, destination=dest, mode="apply", media_type="movie", metadata={})

    def _failing_copy(*_args, **_kwargs):
        raise OSError("copy failed")

    monkeypatch.setattr(executor.shutil, "copy2", _failing_copy)

    result = execute_plans([plan], apply=True, copy_mode=True, on_conflict="overwrite")

    assert result.moved == []
    assert result.errors
    assert dest.read_text(encoding="utf-8") == "old"
    assert src.exists()
    assert not (tmp_path / "dest.mkv.plexify.tmp").exists()


def test_conflict_overwrite_move_keeps_source_when_replace_fails(monkeypatch, tmp_path: Path) -> None:
    src = tmp_path / "source.mkv"
    dest = tmp_path / "dest.mkv"
    src.write_text("data", encoding="utf-8")
    dest.write_text("old", encoding="utf-8")
    plan = MovePlan(source=src, destination=dest, mode="apply", media_type="movie", metadata={})

    def _failing_replace(*args, **kwargs):
        raise OSError("replace failed")

    monkeypatch.setattr(executor.os, "replace", _failing_replace)
    monkeypatch.setattr(executor.shutil, "copy2", executor.shutil.copy2)

    result = execute_plans([plan], apply=True, copy_mode=False, on_conflict="overwrite")

    assert result.moved == []
    assert result.errors
    assert src.exists()
    assert dest.read_text(encoding="utf-8") == "old"
    assert not (tmp_path / "dest.mkv.plexify.tmp").exists()


def test_overwrite_confirmation_summary_mentions_policy(monkeypatch) -> None:
    messages: list[str] = []
    monkeypatch.setattr(cli.console, "print", lambda message, *_args, **_kwargs: messages.append(str(message)))
    monkeypatch.setattr(cli, "_prompt_text", lambda *_args, **_kwargs: "OVERWRITE")

    ok = cli._confirm_overwrite_apply([], copy_mode=True)
    assert ok is True
    assert any("Conflict policy: overwrite" in msg for msg in messages)
