from pathlib import Path

from plexify import cli
from plexify.util import MovePlan


def test_prune_empty_dirs_skips_non_empty_parent(tmp_path: Path) -> None:
    incoming = tmp_path / "incoming"
    season = incoming / "Show" / "Season 1"
    season.mkdir(parents=True)
    moved_file = season / "Episode 1.mkv"
    sibling_file = season / "Episode 2.mkv"
    moved_file.write_text("x", encoding="utf-8")
    sibling_file.write_text("x", encoding="utf-8")

    plan = MovePlan(
        source=moved_file,
        destination=tmp_path / "library" / "TV Shows" / "Show" / "Season 1" / moved_file.name,
        mode="apply",
        media_type="tv",
        metadata={},
    )
    moved_file.unlink()

    cli._prune_empty_dirs([plan], incoming, dry_run=False)

    assert season.exists()
    assert sibling_file.exists()


def test_prune_empty_dirs_removes_directory_with_only_ignored_files(tmp_path: Path) -> None:
    incoming = tmp_path / "incoming"
    season = incoming / "Show" / "Season 1"
    season.mkdir(parents=True)
    moved_file = season / "Episode 1.mkv"
    junk_file = season / "Thumbs.db"
    moved_file.write_text("x", encoding="utf-8")
    junk_file.write_text("junk", encoding="utf-8")

    plan = MovePlan(
        source=moved_file,
        destination=tmp_path / "library" / "TV Shows" / "Show" / "Season 1" / moved_file.name,
        mode="apply",
        media_type="tv",
        metadata={},
    )
    moved_file.unlink()

    cli._prune_empty_dirs([plan], incoming, dry_run=False)

    assert not season.exists()


def test_prune_empty_dirs_respects_custom_ignore_list(tmp_path: Path) -> None:
    incoming = tmp_path / "incoming"
    season = incoming / "Show" / "Season 1"
    season.mkdir(parents=True)
    moved_file = season / "Episode 1.mkv"
    custom_junk = season / "sample.nfo"
    moved_file.write_text("x", encoding="utf-8")
    custom_junk.write_text("junk", encoding="utf-8")

    plan = MovePlan(
        source=moved_file,
        destination=tmp_path / "library" / "TV Shows" / "Show" / "Season 1" / moved_file.name,
        mode="apply",
        media_type="tv",
        metadata={},
    )
    moved_file.unlink()

    cli._prune_empty_dirs([plan], incoming, dry_run=False, ignored_files={"sample.nfo"})

    assert not season.exists()
