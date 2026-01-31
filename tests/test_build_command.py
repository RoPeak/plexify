import shlex
from pathlib import Path

from plexify import cli


def test_build_command_from_config() -> None:
    config = cli.BuildCommandConfig(
        incoming=Path("C:/Media Incoming"),
        library=Path("D:/Plex Library"),
        media_type="movie",
        mode="apply",
        copy_mode=False,
        extensions=[".mkv", ".mp4"],
        min_confidence=0.85,
        limit=5,
        interactive=False,
        print_tree=True,
        show_enrichment=False,
        yes=True,
        no_cache=True,
        cache_file=Path("C:/Cache/cache.json"),
        clear_cache=True,
        report=Path("C:/Reports/report.json"),
        on_conflict="skip",
        prune_empty_dirs=True,
    )
    command = cli._build_command(config)

    assert f"--incoming {shlex.quote(str(Path('C:/Media Incoming')))}" in command
    assert f"--library {shlex.quote(str(Path('D:/Plex Library')))}" in command
    assert "--mode apply" in command
    assert "--move" in command
    assert "--extensions .mkv,.mp4" in command
    assert "--min-confidence 0.85" in command
    assert "--limit 5" in command
    assert "--media-type movie" in command
    assert "--print-tree" in command
    assert "--yes" in command
    assert "--no-cache" in command
    assert f"--cache {shlex.quote(str(Path('C:/Cache/cache.json')))}" in command
    assert "--clear-cache" in command
    assert f"--report {shlex.quote(str(Path('C:/Reports/report.json')))}" in command
    assert "--on-conflict skip" in command
    assert "--prune-empty-dirs" in command
    assert "--no-interactive" in command
