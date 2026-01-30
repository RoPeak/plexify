from pathlib import Path

import requests
from typer.testing import CliRunner

from plexify import cli
from plexify.sources import tvmaze, wikidata


def test_sources_unavailable_does_not_crash(monkeypatch, tmp_path: Path) -> None:
    def _raise(*_args, **_kwargs):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(requests.Session, "get", _raise)
    wikidata._available = True
    wikidata._warned = False
    tvmaze._available = True
    tvmaze._warned = False

    incoming = tmp_path / "incoming"
    library = tmp_path / "library"
    incoming.mkdir()
    library.mkdir()
    (incoming / "Gladiator (2000).mkv").write_text("x", encoding="utf-8")

    plans, errors, _stats = cli._plan_items(
        incoming=incoming,
        library=library,
        mode="dry-run",
        copy_mode=True,
        interactive=False,
        auto_accept=True,
        min_confidence=0.55,
        extensions=cli.DEFAULT_EXTENSIONS,
        cache_path=library / ".plexify" / "cache.json",
        limit=None,
        show_cache=False,
        media_type_filter=None,
        use_cache=True,
        on_conflict="rename",
    )

    assert plans == []
    assert errors == []

    runner = CliRunner()
    result = runner.invoke(cli.app, ["wizard", "--help"])
    assert result.exit_code == 0
