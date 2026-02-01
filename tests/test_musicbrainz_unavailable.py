from pathlib import Path

from typer.testing import CliRunner

from plexify import cli
from plexify.sources import musicbrainz


def test_musicbrainz_unavailable_message(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "incoming"
    album = source / "Artist - Album"
    album.mkdir(parents=True)
    (album / "01 - Artist - Track.flac").write_text("x", encoding="utf-8")

    library = tmp_path / "library"
    monkeypatch.setattr(musicbrainz, "_available", False)
    monkeypatch.setattr(musicbrainz, "_unavailable_reason", "offline")

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        ["music", "--source", str(source), "--library", str(library), "--dry-run"],
    )
    assert result.exit_code == 0
    assert "musicbrainz disabled" in result.output.lower()
    assert "no musicbrainz matches" not in result.output.lower()
