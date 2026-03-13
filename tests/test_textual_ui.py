from __future__ import annotations

import asyncio
import shutil
import uuid
from pathlib import Path

from typer.testing import CliRunner
from textual.widgets import Button, Input

from plexify import cli
from plexify.sources import musicbrainz
from plexify.textual_app import ConfigScreen, PlexifyTextualApp
from plexify.ui_controller import MusicUIConfig, MusicUIController, VideoUIConfig, VideoUIController


def _local_tmp(name: str) -> Path:
    root = Path(".pytest-local-tmp") / "textual-ui"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{name}-{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_cli_help_includes_ui_command() -> None:
    runner = CliRunner()
    result = runner.invoke(cli.app, ["--help"])
    assert result.exit_code == 0
    assert "ui" in result.output


def test_video_ui_controller_scans_and_builds_preview(monkeypatch) -> None:
    def _fake_movie_candidates(*_args, **_kwargs) -> cli.CandidatePage:
        candidate = cli.Candidate(
            title="Movie",
            year=2001,
            source="Wikidata",
            confidence=1.0,
            metadata={"qid": "Q1", "title": "Movie", "year": 2001},
        )
        return cli.CandidatePage(candidates=[candidate], raw_results=[], next_offset=0, has_more=False)

    monkeypatch.setattr(cli, "_movie_candidates", _fake_movie_candidates)

    workspace = _local_tmp("video-controller")
    try:
        incoming = workspace / "incoming"
        library = workspace / "library"
        incoming.mkdir()
        library.mkdir()
        (incoming / "Movie.mkv").write_text("x", encoding="utf-8")

        controller = VideoUIController(VideoUIConfig(incoming=incoming, library=library))
        controller.scan()
        controller.accept_candidate(0, 0)
        preview = controller.build_preview()

        assert len(controller.items) == 1
        assert len(preview.plans) == 1
        assert preview.plans[0].media_type == "movie"
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_music_ui_controller_scans_and_builds_preview(monkeypatch) -> None:
    monkeypatch.setattr(
        musicbrainz,
        "search_releases",
        lambda *_args, **_kwargs: [
            musicbrainz.ReleaseCandidate(
                mbid="mb1",
                title="Album",
                artist="Artist",
                year=2001,
                country="GB",
                score=1.0,
                track_count=1,
            )
        ],
    )
    monkeypatch.setattr(
        musicbrainz,
        "fetch_release_tracks",
        lambda *_args, **_kwargs: [musicbrainz.Track(number=1, title="Song One", disc=1)],
    )
    monkeypatch.setattr(musicbrainz, "is_available", lambda: True)

    workspace = _local_tmp("music-controller")
    try:
        source = workspace / "source"
        library = workspace / "library"
        album_dir = source / "Artist" / "Album"
        album_dir.mkdir(parents=True)
        library.mkdir()
        (album_dir / "01 - Song One.flac").write_text("x", encoding="utf-8")

        controller = MusicUIController(MusicUIConfig(source=source, library=library))
        controller.scan()
        controller.select_candidate(0, 0)
        preview = controller.build_preview()

        assert len(controller.albums) == 1
        assert len(preview.plans) == 1
        assert preview.plans[0].media_type == "music"
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_textual_video_flow_to_result(monkeypatch) -> None:
    def _fake_movie_candidates(*_args, **_kwargs) -> cli.CandidatePage:
        candidate = cli.Candidate(
            title="Movie",
            year=2001,
            source="Wikidata",
            confidence=1.0,
            metadata={"qid": "Q1", "title": "Movie", "year": 2001},
        )
        return cli.CandidatePage(candidates=[candidate], raw_results=[], next_offset=0, has_more=False)

    monkeypatch.setattr(cli, "_movie_candidates", _fake_movie_candidates)

    workspace = _local_tmp("video-app")
    try:
        incoming = workspace / "incoming"
        library = workspace / "library"
        incoming.mkdir()
        library.mkdir()
        (incoming / "Movie.mkv").write_text("x", encoding="utf-8")

        async def _run() -> None:
            app = PlexifyTextualApp()
            async with app.run_test() as pilot:
                app.push_screen(ConfigScreen("video"))
                await pilot.pause()
                screen = app.screen
                screen.query_one("#path-one", Input).value = str(incoming)
                screen.query_one("#path-two", Input).value = str(library)
                screen.query_one("#scan", Button).press()
                for _ in range(20):
                    await pilot.pause()
                    if app.screen.__class__.__name__ == "ReviewScreen":
                        break
                assert app.screen.__class__.__name__ == "ReviewScreen"
                review = app.screen
                review.query_one("#accept", Button).press()
                review.query_one("#preview", Button).press()
                await pilot.pause()
                assert app.screen.__class__.__name__ == "PreviewScreen"
                app.screen.query_one("#apply", Button).press()
                for _ in range(20):
                    await pilot.pause()
                    if app.screen.__class__.__name__ == "ResultScreen":
                        break
                assert app.screen.__class__.__name__ == "ResultScreen"

        asyncio.run(_run())
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_textual_music_flow_to_preview(monkeypatch) -> None:
    monkeypatch.setattr(
        musicbrainz,
        "search_releases",
        lambda *_args, **_kwargs: [
            musicbrainz.ReleaseCandidate(
                mbid="mb1",
                title="Album",
                artist="Artist",
                year=2001,
                country="GB",
                score=1.0,
                track_count=1,
            )
        ],
    )
    monkeypatch.setattr(
        musicbrainz,
        "fetch_release_tracks",
        lambda *_args, **_kwargs: [musicbrainz.Track(number=1, title="Song One", disc=1)],
    )
    monkeypatch.setattr(musicbrainz, "is_available", lambda: True)

    workspace = _local_tmp("music-app")
    try:
        source = workspace / "source"
        library = workspace / "library"
        album_dir = source / "Artist" / "Album"
        album_dir.mkdir(parents=True)
        library.mkdir()
        (album_dir / "01 - Song One.flac").write_text("x", encoding="utf-8")

        async def _run() -> None:
            app = PlexifyTextualApp()
            async with app.run_test() as pilot:
                app.push_screen(ConfigScreen("music"))
                await pilot.pause()
                screen = app.screen
                screen.query_one("#path-one", Input).value = str(source)
                screen.query_one("#path-two", Input).value = str(library)
                screen.query_one("#scan", Button).press()
                for _ in range(20):
                    await pilot.pause()
                    if app.screen.__class__.__name__ == "ReviewScreen":
                        break
                assert app.screen.__class__.__name__ == "ReviewScreen"
                review = app.screen
                review.query_one("#accept", Button).press()
                review.query_one("#preview", Button).press()
                await pilot.pause()
                assert app.screen.__class__.__name__ == "PreviewScreen"

        asyncio.run(_run())
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
