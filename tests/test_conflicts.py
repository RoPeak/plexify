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

    monkeypatch.setattr(executor, "_copy_with_progress", _failing_copy)

    result = execute_plans([plan], apply=True, copy_mode=True, on_conflict="overwrite")

    assert result.moved == []
    assert result.errors
    assert dest.read_text(encoding="utf-8") == "old"
    assert src.exists()
    assert list(tmp_path.glob(".dest.mkv.plexify-*.tmp")) == []


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
    assert list(tmp_path.glob(".dest.mkv.plexify-*.tmp")) == []


def test_overwrite_confirmation_summary_mentions_policy(monkeypatch) -> None:
    messages: list[str] = []
    monkeypatch.setattr(cli.console, "print", lambda message, *_args, **_kwargs: messages.append(str(message)))
    monkeypatch.setattr(cli, "_prompt_text", lambda *_args, **_kwargs: "OVERWRITE")

    ok = cli._confirm_overwrite_apply([], copy_mode=True)
    assert ok is True
    assert any("Conflict policy: overwrite" in msg for msg in messages)


def test_copy_failure_never_exposes_partial_final_path(monkeypatch, tmp_path: Path) -> None:
    src = tmp_path / "source.mkv"
    dest = tmp_path / "dest.mkv"
    src.write_bytes(b"complete source")
    plan = MovePlan(source=src, destination=dest, mode="apply", media_type="movie", metadata={})

    def _partial_then_fail(_source, temporary, **_kwargs):
        assert temporary != dest
        assert temporary.name.startswith(".dest.mkv.plexify-")
        assert temporary.suffix == ".tmp"
        temporary.write_bytes(b"partial")
        raise OSError("simulated interrupted copy")

    monkeypatch.setattr(executor, "_copy_with_progress", _partial_then_fail)
    result = execute_plans([plan], apply=True, copy_mode=True, on_conflict="rename")

    assert result.moved == []
    assert result.errors
    assert src.read_bytes() == b"complete source"
    assert not dest.exists()
    assert list(tmp_path.glob(".dest.mkv.plexify-*.tmp")) == []


def test_copy_rename_is_no_clobber_when_destination_appears_during_copy(monkeypatch, tmp_path: Path) -> None:
    src = tmp_path / "source.mkv"
    dest = tmp_path / "dest.mkv"
    src.write_bytes(b"ours")
    plan = MovePlan(source=src, destination=dest, mode="apply", media_type="movie", metadata={})
    real_copy = executor._copy_with_progress

    def _copy_then_race(source, temporary, **kwargs):
        copied = real_copy(source, temporary, **kwargs)
        dest.write_bytes(b"other process")
        return copied

    monkeypatch.setattr(executor, "_copy_with_progress", _copy_then_race)
    result = execute_plans([plan], apply=True, copy_mode=True, on_conflict="rename")

    assert not result.errors
    assert len(result.moved) == 1
    assert result.moved[0].destination != dest
    assert dest.read_bytes() == b"other process"
    assert result.moved[0].destination.read_bytes() == b"ours"
    assert src.read_bytes() == b"ours"
