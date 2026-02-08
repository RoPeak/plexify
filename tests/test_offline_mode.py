from pathlib import Path

import requests
from typer.testing import CliRunner

from plexify import cli
from plexify.cache import Cache
from plexify.infer import InferredItem
from plexify.sources import musicbrainz


def test_tv_candidates_offline_does_not_call_network(monkeypatch, tmp_path: Path) -> None:
    item = InferredItem(
        path=Path("incoming/Show/Season 1/Show.S01E01.mkv"),
        media_type="tv",
        title="Show",
        year=None,
        season=1,
        episode=1,
    )
    cache = Cache(tmp_path / "cache.json")
    monkeypatch.setattr(
        cli.tvmaze,
        "search_shows",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network should not be called")),
    )

    page = cli._tv_candidates(
        item,
        session=requests.Session(),
        cache=cache,
        show_cache=False,
        cache_key="tv|path|show|unknown",
        incoming_root=Path("incoming"),
        offline=True,
    )

    assert page.cache_hit is False
    assert page.candidates == []


def test_movie_candidates_offline_does_not_call_network(monkeypatch, tmp_path: Path) -> None:
    item = InferredItem(
        path=Path("incoming/Movie.mkv"),
        media_type="movie",
        title="Movie",
        year=None,
    )
    cache = Cache(tmp_path / "cache.json")
    monkeypatch.setattr(
        cli.wikidata,
        "search",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network should not be called")),
    )

    page = cli._movie_candidates(
        item,
        session=requests.Session(),
        cache=cache,
        show_cache=False,
        cache_key="movie|path|movie|unknown",
        offline=True,
    )

    assert page.cache_hit is False
    assert page.candidates == []


def test_music_offline_disables_musicbrainz_verification(monkeypatch) -> None:
    source = Path("plexify")
    library = Path("tests")

    monkeypatch.setattr(
        cli.music_util,
        "discover_albums",
        lambda *_args, **_kwargs: (
            [
                cli.music_util.AlbumGroup(
                    source=source,
                    artist="Artist",
                    album="Album",
                    tracks=[
                        cli.music_util.TrackInfo(
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
            ],
            [],
        ),
    )
    monkeypatch.setattr(cli, "_save_wizard_prefs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "write_report", lambda *_args, **_kwargs: None)

    monkeypatch.setattr(
        musicbrainz,
        "search_releases",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network should not be called")),
    )

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        ["music", "--source", str(source), "--library", str(library), "--dry-run", "--offline"],
    )
    assert result.exit_code == 0
    assert "Offline mode: MusicBrainz verification disabled for this run." in result.output
