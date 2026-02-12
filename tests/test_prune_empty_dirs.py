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


def test_music_sidecar_cleanup_allows_folder_prune(tmp_path: Path) -> None:
    incoming = tmp_path / "incoming"
    album_dir = incoming / "Artist - Album"
    album_dir.mkdir(parents=True)
    moved_file = album_dir / "01 - Artist - Track.flac"
    log_file = album_dir / "Artist - Album.log"
    moved_file.write_text("x", encoding="utf-8")
    log_file.write_text("log", encoding="utf-8")

    plan = MovePlan(
        source=moved_file,
        destination=tmp_path / "library" / "Music" / "Artist" / "Album" / moved_file.name,
        mode="apply",
        media_type="music",
        metadata={},
    )
    moved_file.unlink()

    album = cli.music_util.AlbumGroup(
        source=album_dir,
        artist="Artist",
        album="Album",
        tracks=[],
        images=[],
        cues=[],
        logs=[log_file],
    )
    removed, warnings = cli._remove_skipped_music_sidecars([album], keep_cue=False, keep_log=False)

    assert removed == 1
    assert warnings == []
    cli._prune_empty_dirs([plan], incoming, dry_run=False)
    assert not album_dir.exists()


def test_music_sidecar_cleanup_respects_keep_flags(tmp_path: Path) -> None:
    incoming = tmp_path / "incoming"
    album_dir = incoming / "Artist - Album"
    album_dir.mkdir(parents=True)
    moved_file = album_dir / "01 - Artist - Track.flac"
    log_file = album_dir / "Artist - Album.log"
    moved_file.write_text("x", encoding="utf-8")
    log_file.write_text("log", encoding="utf-8")

    plan = MovePlan(
        source=moved_file,
        destination=tmp_path / "library" / "Music" / "Artist" / "Album" / moved_file.name,
        mode="apply",
        media_type="music",
        metadata={},
    )
    moved_file.unlink()

    album = cli.music_util.AlbumGroup(
        source=album_dir,
        artist="Artist",
        album="Album",
        tracks=[],
        images=[],
        cues=[],
        logs=[log_file],
    )
    removed, warnings = cli._remove_skipped_music_sidecars([album], keep_cue=False, keep_log=True)

    assert removed == 0
    assert warnings == []
    assert log_file.exists()
    cli._prune_empty_dirs([plan], incoming, dry_run=False)
    assert album_dir.exists()
