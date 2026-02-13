from pathlib import Path

from plexify import cli, music
from plexify.sources import musicbrainz
from plexify.util import MovePlan


def test_music_discover_and_destination(tmp_path: Path) -> None:
    source = tmp_path / "incoming"
    album = source / "Alanis Morissette - Jagged Little Pill"
    album.mkdir(parents=True)
    track = album / "01 - Alanis Morissette - All I Really Want.flac"
    track.write_text("x", encoding="utf-8")

    albums, errors = music.discover_albums(source, ["flac"])
    assert errors == []
    assert len(albums) == 1
    planned_tracks = cli._music_tracks_from_filenames(albums[0].tracks)
    assert planned_tracks[0].track_number_text == "01"

    library = tmp_path / "library"
    destination = music.track_destination(
        library,
        albums[0].artist,
        albums[0].album,
        planned_tracks[0].track_number_text,
        planned_tracks[0].track_title,
        planned_tracks[0].ext,
    )
    assert destination.name == "01 - All I Really Want.flac"
    assert destination.parent.name == "Jagged Little Pill"
    assert destination.parent.parent.name == "Alanis Morissette"
    assert destination.parent.parent.parent.name == "Music"


def test_music_discover_recursive_artist_album_year_with_two_part_tracks(tmp_path: Path) -> None:
    source = tmp_path / "incoming"
    album = source / "Carole King" / "Tapestry (1971)"
    album.mkdir(parents=True)
    (album / "01 - I Feel the Earth Move.flac").write_text("x", encoding="utf-8")

    albums, errors = music.discover_albums(source, ["flac"])
    assert errors == []
    assert len(albums) == 1
    assert albums[0].artist == "Carole King"
    assert albums[0].album == "Tapestry"
    assert albums[0].year == 1971
    assert albums[0].tracks[0].track_artist == "Carole King"


def test_music_discover_flat_folder_uses_dominant_track_artist(tmp_path: Path) -> None:
    source = tmp_path / "incoming"
    album = source / "Bing Crosby & Friends"
    album.mkdir(parents=True)
    (album / "01 - Bing Crosby - First.flac").write_text("x", encoding="utf-8")
    (album / "02 - Bing Crosby feat. X - Second.flac").write_text("x", encoding="utf-8")
    (album / "03 - Bing Crosby - Third.flac").write_text("x", encoding="utf-8")
    (album / "04 - Bing Crosby - Fourth.flac").write_text("x", encoding="utf-8")
    (album / "05 - Guest Singer - Fifth.flac").write_text("x", encoding="utf-8")

    albums, errors = music.discover_albums(source, ["flac"])
    assert errors == []
    assert len(albums) == 1
    assert albums[0].artist == "Bing Crosby"
    assert albums[0].album == "Bing Crosby & Friends"


def test_music_discover_flat_folder_year_suffix_is_preserved_as_metadata(tmp_path: Path) -> None:
    source = tmp_path / "incoming"
    album = source / "Sampler (2001)"
    album.mkdir(parents=True)
    (album / "01 - Artist - One.flac").write_text("x", encoding="utf-8")
    (album / "02 - Artist - Two.flac").write_text("x", encoding="utf-8")
    (album / "03 - Artist - Three.flac").write_text("x", encoding="utf-8")
    (album / "04 - Artist - Four.flac").write_text("x", encoding="utf-8")
    (album / "05 - Guest - Five.flac").write_text("x", encoding="utf-8")

    albums, errors = music.discover_albums(source, ["flac"])
    assert errors == []
    assert len(albums) == 1
    assert albums[0].album == "Sampler"
    assert albums[0].year == 2001


def test_music_discover_flat_folder_skips_ambiguous_artist(tmp_path: Path) -> None:
    source = tmp_path / "incoming"
    album = source / "Sampler"
    album.mkdir(parents=True)
    (album / "01 - Artist One - Track One.flac").write_text("x", encoding="utf-8")
    (album / "02 - Artist Two - Track Two.flac").write_text("x", encoding="utf-8")

    albums, errors = music.discover_albums(source, ["flac"])
    assert albums == []
    assert any("ambiguous album artist" in error.lower() for error in errors)


def test_parse_track_filename_supports_two_part_with_default_artist() -> None:
    path = Path("01 - Track One.flac")
    parsed = music.parse_track_filename(path, default_artist="Artist")
    assert parsed is not None
    assert parsed.track_number == 1
    assert parsed.track_artist == "Artist"
    assert parsed.track_title == "Track One"


def test_musicbrainz_mapping_single_disc() -> None:
    tracks = [
        music.TrackInfo(
            source=Path("01 - Artist - Track One.flac"),
            track_number=1,
            track_title="Track One",
            track_artist="Artist",
            ext=".flac",
        )
    ]
    mb_tracks = [musicbrainz.Track(number=1, title="Track One (MB)", disc=1)]
    mapped, reason = cli._map_musicbrainz_tracks(tracks, mb_tracks)
    assert reason is None
    assert mapped is not None
    assert mapped[0].track_title == "Track One (MB)"


def test_musicbrainz_mapping_requires_disc_numbers() -> None:
    tracks = [
        music.TrackInfo(
            source=Path("01 - Artist - Track One.flac"),
            track_number=1,
            track_title="Track One",
            track_artist="Artist",
            ext=".flac",
        )
    ]
    mb_tracks = [musicbrainz.Track(number=1, title="Track One", disc=2)]
    mapped, reason = cli._map_musicbrainz_tracks(tracks, mb_tracks)
    assert mapped is None
    assert reason == "Multi-disc release without disc numbers in filenames"


def test_track_destination_untitled_fallback(tmp_path: Path) -> None:
    destination = music.track_destination(
        tmp_path,
        "Oasis",
        "Album",
        "06",
        "[untitled]",
        ".flac",
    )
    assert destination.name == "06 - Untitled.flac" or destination.name == "06 - Track 06.flac"


def test_should_use_various_artists_ignores_featured_artists() -> None:
    album = music.AlbumGroup(
        source=Path("Eminem - Curtain Call"),
        artist="Eminem",
        album="Curtain Call",
        tracks=[
            music.TrackInfo(
                source=Path("01 - Eminem - Intro.flac"),
                track_number=1,
                track_title="Intro",
                track_artist="Eminem",
                ext=".flac",
            ),
            music.TrackInfo(
                source=Path("02 - Eminem feat. Dido - Stan.flac"),
                track_number=2,
                track_title="Stan",
                track_artist="Eminem feat. Dido",
                ext=".flac",
            ),
        ],
        images=[],
        cues=[],
        logs=[],
    )
    assert cli._should_use_various_artists(album, "Eminem") is False


def test_music_track_preview_prints_limit_and_remainder(monkeypatch) -> None:
    lines: list[str] = []
    monkeypatch.setattr(cli.console, "print", lambda message: lines.append(str(message)))
    plans = [
        MovePlan(
            source=Path(f"in/{index:02d}.flac"),
            destination=Path(f"out/{index:02d}.flac"),
            mode="dry-run",
            media_type="music",
            metadata={},
        )
        for index in range(1, 5)
    ]

    cli._print_music_track_previews(plans, limit=2)

    assert lines[0] == "Track preview (2/4):"
    assert lines[1] == "- 01.flac -> 01.flac"
    assert lines[2] == "- 02.flac -> 02.flac"
    assert lines[3] == "... +2 more track(s)"
