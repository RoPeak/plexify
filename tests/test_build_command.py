from pathlib import Path

from plexify import cli
from plexify.command_builder import quote_cli_arg


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
        quiet=True,
        prune_ignore="Thumbs.db,desktop.ini",
    )
    command = cli._build_command(config)

    assert f"--incoming {quote_cli_arg(str(Path('C:/Media Incoming')))}" in command
    assert f"--library {quote_cli_arg(str(Path('D:/Plex Library')))}" in command
    assert "--mode apply" in command
    assert "--move" in command
    assert f"--extensions {quote_cli_arg('.mkv,.mp4')}" in command
    assert "--min-confidence 0.85" in command
    assert "--limit 5" in command
    assert "--media-type movie" in command
    assert "--print-tree" in command
    assert "--yes" in command
    assert "--no-cache" in command
    assert f"--cache {quote_cli_arg(str(Path('C:/Cache/cache.json')))}" in command
    assert "--clear-cache" in command
    assert f"--report {quote_cli_arg(str(Path('C:/Reports/report.json')))}" in command
    assert "--on-conflict skip" in command
    assert "--prune-empty-dirs" in command
    assert "--quiet" in command
    assert f"--prune-ignore {quote_cli_arg('Thumbs.db,desktop.ini')}" in command
    assert "--no-interactive" in command


def test_build_command_escapes_apostrophes_for_windows_shells() -> None:
    config = cli.BuildCommandConfig(
        incoming=Path("C:/Ronan's Incoming"),
        library=Path("D:/Plex"),
        media_type="tv",
        mode="apply",
        copy_mode=True,
        extensions=cli.DEFAULT_EXTENSIONS_LIST,
        min_confidence=cli.DEFAULT_MIN_CONFIDENCE,
        limit=None,
        interactive=True,
        print_tree=False,
        show_enrichment=False,
        yes=False,
        no_cache=False,
        cache_file=None,
        clear_cache=False,
        report=None,
        on_conflict="rename",
        prune_empty_dirs=False,
        quiet=False,
        prune_ignore=cli.DEFAULT_PRUNE_IGNORE,
    )

    command = cli._build_command(config)
    incoming_quoted = quote_cli_arg(str(Path("C:/Ronan's Incoming")))

    assert f"--incoming {incoming_quoted}" in command


def test_build_command_includes_allow_risky_enter_accept_flag() -> None:
    config = cli.BuildCommandConfig(
        incoming=Path("C:/Incoming"),
        library=Path("D:/Library"),
        media_type="auto",
        mode="dry-run",
        copy_mode=True,
        extensions=cli.DEFAULT_EXTENSIONS_LIST,
        min_confidence=cli.DEFAULT_MIN_CONFIDENCE,
        limit=None,
        interactive=True,
        print_tree=False,
        show_enrichment=False,
        yes=False,
        no_cache=False,
        cache_file=None,
        clear_cache=False,
        report=None,
        on_conflict="rename",
        prune_empty_dirs=False,
        quiet=False,
        prune_ignore=cli.DEFAULT_PRUNE_IGNORE,
        allow_risky_enter_accept=True,
    )

    command = cli._build_command(config)
    assert "--allow-risky-enter-accept" in command
