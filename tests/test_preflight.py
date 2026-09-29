from pathlib import Path

import pytest

from plexify.preflight import PreflightError, validate_paths


def test_preflight_checks_without_creating_missing_library(tmp_path: Path) -> None:
    source = tmp_path / "incoming"
    source.mkdir()
    missing = tmp_path / "Movis"
    with pytest.raises(PreflightError, match="does not exist"):
        validate_paths(source, missing, apply=True)
    assert not missing.exists()


def test_preflight_rejects_overlapping_paths(tmp_path: Path) -> None:
    source = tmp_path / "library" / "incoming"
    source.mkdir(parents=True)
    library = source.parent
    with pytest.raises(PreflightError, match="inside Library"):
        validate_paths(source, library, apply=False)


def test_preflight_reports_same_filesystem_and_accepts_read_only_dry_run(tmp_path: Path) -> None:
    source = tmp_path / "incoming"
    library = tmp_path / "library"
    source.mkdir()
    library.mkdir()
    result = validate_paths(source, library, apply=False, require_same_filesystem=True)
    assert result.same_filesystem


def test_preflight_enforces_same_filesystem_policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "incoming"
    library = tmp_path / "library"
    source.mkdir()
    library.mkdir()
    class DifferentDevice:
        def __init__(self, dev: int) -> None:
            self.st_dev = dev
    monkeypatch.setattr(Path, "stat", lambda self, *args, **kwargs: DifferentDevice(1 if self == source.resolve() else 2))
    with pytest.raises(PreflightError, match="different filesystems"):
        validate_paths(source, library, apply=False, require_same_filesystem=True)
