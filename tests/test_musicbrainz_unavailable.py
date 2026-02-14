from pathlib import Path

from typer.testing import CliRunner

from plexify import cli
from plexify.cache import Cache
from plexify.sources import musicbrainz


def test_musicbrainz_unavailable_message(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "incoming"
    album = source / "Artist - Album"
    album.mkdir(parents=True)
    (album / "01 - Artist - Track.flac").write_text("x", encoding="utf-8")

    library = tmp_path / "library"
    monkeypatch.setattr(musicbrainz, "_available", False)
    monkeypatch.setattr(musicbrainz, "_unavailable_reason", "offline")
    monkeypatch.setattr(musicbrainz, "_recover_at", None)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        ["music", "--source", str(source), "--library", str(library), "--dry-run"],
    )
    assert result.exit_code == 0
    assert "musicbrainz disabled" in result.output.lower()
    assert "no musicbrainz matches" not in result.output.lower()


def test_music_replays_filename_fallback_when_musicbrainz_unavailable(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "incoming"
    album = source / "Artist" / "Album"
    album.mkdir(parents=True)
    (album / "01 - Song One.flac").write_text("x", encoding="utf-8")
    library = tmp_path / "library"
    library.mkdir()

    albums, errors = cli.music_util.discover_albums(source, ["flac"])
    assert errors == []
    key = cli.music_util.album_decision_cache_key(albums[0])
    cache = Cache(library / ".plexify" / "cache.json")
    cache.set_music(
        key,
        {
            "version": 1,
            "selection_mode": "manual",
            "decision": "filename_fallback",
            "created_at": "2026-01-01_00-00-00",
        },
    )
    cache.save()

    monkeypatch.setattr(musicbrainz, "_available", False)
    monkeypatch.setattr(musicbrainz, "_unavailable_reason", "offline")
    monkeypatch.setattr(musicbrainz, "_recover_at", None)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        ["music", "--source", str(source), "--library", str(library), "--dry-run"],
    )
    assert result.exit_code == 0
    assert "reused cached music decision for this album." in result.output.lower()
