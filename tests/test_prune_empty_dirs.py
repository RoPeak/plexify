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

    cli._prune_empty_dirs([plan], incoming, dry_run=False)

    assert season.exists()
    assert sibling_file.exists()
