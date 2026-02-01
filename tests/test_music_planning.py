from pathlib import Path

from plexify import cli, music
from plexify.sources import musicbrainz


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
