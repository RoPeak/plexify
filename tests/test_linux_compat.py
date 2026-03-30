from __future__ import annotations

from pathlib import Path

import pytest

from plexify import cli
from plexify.paths import validate_non_overlapping
from plexify.runtime_platform import PLEXIFY_PLATFORM_ENV, resolve_platform


def test_platform_auto_uses_detected_platform() -> None:
    context = resolve_platform("auto", env={})
    assert context.effective_platform in {"windows", "linux"}
    assert context.override_source is None


def test_platform_env_override_applies_when_cli_auto(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(PLEXIFY_PLATFORM_ENV, "linux")
    context = resolve_platform("auto")
    assert context.effective_platform == "linux"
    assert context.override_source == "env"


def test_platform_cli_override_takes_precedence_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(PLEXIFY_PLATFORM_ENV, "linux")
    context = resolve_platform("windows")
    assert context.effective_platform == "windows"
    assert context.override_source == "cli"


def test_invalid_platform_value_raises() -> None:
    with pytest.raises(ValueError):
        resolve_platform("beos")


def test_windows_overlap_is_case_insensitive() -> None:
    ok, _reason, _suggestion = validate_non_overlapping(
        Path("C:\\Media\\Incoming"),
        Path("c:\\media\\incoming"),
        platform="windows",
    )
    assert ok is False


def test_linux_overlap_is_case_sensitive() -> None:
    ok, reason, suggestion = validate_non_overlapping(
        Path("/Media/Incoming"),
        Path("/media/incoming"),
        platform="linux",
    )
    assert ok is True
    assert reason == ""
    assert suggestion is None


def test_collision_lookup_windows_is_case_insensitive() -> None:
    destination = Path("C:/Library/Movie.mkv")
    planned = {"c:/library/movie.mkv": 1}
    resolved, changed = cli._resolve_destination(destination, "rename", planned, None, platform="windows")
    assert changed is True
    assert resolved is not None
    assert resolved.stem.endswith("(2)")


def test_collision_lookup_linux_preserves_case() -> None:
    destination = Path("/library/Movie.mkv")
    planned = {"/library/movie.mkv": 1}
    resolved, changed = cli._resolve_destination(destination, "rename", planned, None, platform="linux")
    assert changed is False
    assert resolved == destination
