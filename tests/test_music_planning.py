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


def test_music_discover_nested_album_with_dash_keeps_parent_artist(tmp_path: Path) -> None:
    source = tmp_path / "incoming"
    album = source / "Take That" / "Never Forget - The Ultimate Collection"
    album.mkdir(parents=True)
    (album / "01 - Never Forget.flac").write_text("x", encoding="utf-8")

    albums, errors = music.discover_albums(source, ["flac"])
    assert errors == []
    assert len(albums) == 1
    assert albums[0].artist == "Take That"
    assert albums[0].album == "Never Forget - The Ultimate Collection"


def test_music_discover_nested_artist_album_prefix_matching_parent(tmp_path: Path) -> None:
    source = tmp_path / "incoming"
    album = source / "Take That" / "Take That - III"
    album.mkdir(parents=True)
    (album / "01 - Patience.flac").write_text("x", encoding="utf-8")

    albums, errors = music.discover_albums(source, ["flac"])
    assert errors == []
    assert len(albums) == 1
    assert albums[0].artist == "Take That"
    assert albums[0].album == "III"


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


def test_music_discover_reports_invalid_track_examples_and_keeps_valid_tracks(tmp_path: Path) -> None:
    source = tmp_path / "incoming"
    album = source / "Artist - Album"
    album.mkdir(parents=True)
    (album / "01 - Artist - First.flac").write_text("x", encoding="utf-8")
    (album / "02 - Artist - Second.flac").write_text("x", encoding="utf-8")
    (album / "bad-name.flac").write_text("x", encoding="utf-8")
    (album / "also bad.flac").write_text("x", encoding="utf-8")

    albums, errors = music.discover_albums(source, ["flac"])

    assert len(albums) == 1
    assert len(albums[0].tracks) == 2
    assert albums[0].invalid_track_count == 2
    assert "bad-name.flac" in albums[0].invalid_track_examples
    assert any("Planned with valid tracks only." in error for error in errors)
    assert any("bad-name.flac" in error for error in errors)


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


def test_music_discover_disc_suffix_keeps_base_album_and_disc_number(tmp_path: Path) -> None:
    source = tmp_path / "incoming"
    disc_one = source / "Various" / "Ultimate Disney - CD 1 (2004)"
    disc_two = source / "Various" / "Ultimate Disney - CD 2 (2004)"
    disc_one.mkdir(parents=True)
    disc_two.mkdir(parents=True)
    (disc_one / "01 - Circle of Life.flac").write_text("x", encoding="utf-8")
    (disc_two / "01 - Hakuna Matata.flac").write_text("x", encoding="utf-8")

    albums, errors = music.discover_albums(source, ["flac"])
    assert errors == []
    assert len(albums) == 2
    albums_sorted = sorted(albums, key=lambda entry: entry.disc_number or 0)
    assert albums_sorted[0].album == "Ultimate Disney"
    assert albums_sorted[0].year == 2004
    assert albums_sorted[0].disc_number == 1
    assert albums_sorted[1].album == "Ultimate Disney"
    assert albums_sorted[1].year == 2004
    assert albums_sorted[1].disc_number == 2


def test_music_discover_flat_folder_skips_ambiguous_artist(tmp_path: Path) -> None:
    source = tmp_path / "incoming"
    album = source / "Sampler"
    album.mkdir(parents=True)
    (album / "01 - Artist One - Track One.flac").write_text("x", encoding="utf-8")
    (album / "02 - Artist Two - Track Two.flac").write_text("x", encoding="utf-8")

    albums, errors = music.discover_albums(source, ["flac"])
    assert albums == []
    assert any("ambiguous album artist" in error.lower() for error in errors)


def test_music_tracks_from_filenames_uses_disc_prefix_in_multidisc_context() -> None:
    tracks = [
        music.TrackInfo(
            source=Path("01 - Artist - Track One.flac"),
            track_number=1,
            track_title="Track One",
            track_artist="Artist",
            ext=".flac",
        )
    ]
    planned = cli._music_tracks_from_filenames(tracks, disc_number=2, multi_disc=True)
    assert planned[0].track_number == 201
    assert planned[0].track_number_text == "201"
    assert planned[0].disc_number == 2


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


def test_should_use_various_artists_uses_dominance_when_source_artist_is_generic() -> None:
    dominant_album = music.AlbumGroup(
        source=Path("Various Artists - Mylo Xyloto"),
        artist="Various Artists",
        album="Mylo Xyloto",
        tracks=[
            music.TrackInfo(
                source=Path("01 - Coldplay - One.flac"),
                track_number=1,
                track_title="One",
                track_artist="Coldplay",
                ext=".flac",
            ),
            music.TrackInfo(
                source=Path("02 - Coldplay - Two.flac"),
                track_number=2,
                track_title="Two",
                track_artist="Coldplay",
                ext=".flac",
            ),
            music.TrackInfo(
                source=Path("03 - Coldplay - Three.flac"),
                track_number=3,
                track_title="Three",
                track_artist="Coldplay",
                ext=".flac",
            ),
            music.TrackInfo(
                source=Path("04 - Guest - Four.flac"),
                track_number=4,
                track_title="Four",
                track_artist="Guest",
                ext=".flac",
            ),
            music.TrackInfo(
                source=Path("05 - Coldplay - Five.flac"),
                track_number=5,
                track_title="Five",
                track_artist="Coldplay",
                ext=".flac",
            ),
        ],
        images=[],
        cues=[],
        logs=[],
    )
    mixed_album = music.AlbumGroup(
        source=Path("Various Artists - Mixed"),
        artist="Various Artists",
        album="Mixed",
        tracks=[
            music.TrackInfo(
                source=Path("01 - Artist A - One.flac"),
                track_number=1,
                track_title="One",
                track_artist="Artist A",
                ext=".flac",
            ),
            music.TrackInfo(
                source=Path("02 - Artist B - Two.flac"),
                track_number=2,
                track_title="Two",
                track_artist="Artist B",
                ext=".flac",
            ),
            music.TrackInfo(
                source=Path("03 - Artist A - Three.flac"),
                track_number=3,
                track_title="Three",
                track_artist="Artist A",
                ext=".flac",
            ),
            music.TrackInfo(
                source=Path("04 - Artist C - Four.flac"),
                track_number=4,
                track_title="Four",
                track_artist="Artist C",
                ext=".flac",
            ),
            music.TrackInfo(
                source=Path("05 - Artist D - Five.flac"),
                track_number=5,
                track_title="Five",
                track_artist="Artist D",
                ext=".flac",
            ),
        ],
        images=[],
        cues=[],
        logs=[],
    )

    assert cli._should_use_various_artists(dominant_album, "Coldplay") is False
    assert cli._should_use_various_artists(mixed_album, "Coldplay") is True


def test_should_use_various_artists_when_candidate_artist_is_generic() -> None:
    album = music.AlbumGroup(
        source=Path("Artist - Album"),
        artist="Artist",
        album="Album",
        tracks=[
            music.TrackInfo(
                source=Path("01 - Artist - Track.flac"),
                track_number=1,
                track_title="Track",
                track_artist="Artist",
                ext=".flac",
            )
        ],
        images=[],
        cues=[],
        logs=[],
    )
    assert cli._should_use_various_artists(album, "Various Artists") is True


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


def test_album_decision_cache_key_changes_with_track_count(tmp_path: Path) -> None:
    source = tmp_path / "incoming"
    album_dir = source / "Artist" / "Album"
    album_dir.mkdir(parents=True)
    one = album_dir / "01 - Track One.flac"
    two = album_dir / "02 - Track Two.flac"
    one.write_text("x", encoding="utf-8")
    two.write_text("x", encoding="utf-8")

    albums, errors = music.discover_albums(source, ["flac"])
    assert errors == []
    assert len(albums) == 1
    key_one_track = music.album_decision_cache_key(
        music.AlbumGroup(
            source=albums[0].source,
            artist=albums[0].artist,
            album=albums[0].album,
            tracks=albums[0].tracks[:1],
            images=[],
            cues=[],
            logs=[],
            year=albums[0].year,
            disc_number=albums[0].disc_number,
        )
    )
    key_two_tracks = music.album_decision_cache_key(albums[0])

    assert key_one_track != key_two_tracks


def test_album_decision_cache_key_changes_with_track_title_hash(tmp_path: Path) -> None:
    source = tmp_path / "incoming"
    album_dir = source / "Artist" / "Album"
    album_dir.mkdir(parents=True)
    (album_dir / "01 - Track One.flac").write_text("x", encoding="utf-8")

    albums, errors = music.discover_albums(source, ["flac"])
    assert errors == []
    assert len(albums) == 1
    base_album = albums[0]
    key_original = music.album_decision_cache_key(base_album)
    changed_track = music.TrackInfo(
        source=base_album.tracks[0].source,
        track_number=base_album.tracks[0].track_number,
        track_title="Different Title",
        track_artist=base_album.tracks[0].track_artist,
        ext=base_album.tracks[0].ext,
    )
    modified_album = music.AlbumGroup(
        source=base_album.source,
        artist=base_album.artist,
        album=base_album.album,
        tracks=[changed_track],
        images=[],
        cues=[],
        logs=[],
        year=base_album.year,
        disc_number=base_album.disc_number,
    )
    key_changed = music.album_decision_cache_key(modified_album)

    assert key_original != key_changed
