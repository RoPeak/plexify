from __future__ import annotations

import asyncio
import shutil
import uuid
from pathlib import Path

from typer.testing import CliRunner
from textual.css.query import NoMatches
from textual.widgets import Button, Checkbox, Input, Static

from plexify import cli, ui_services
from plexify import music as music_util
from plexify.sources import musicbrainz
from plexify import ui_controller
from plexify.textual_app import ConfigScreen, PlexifyTextualApp
from plexify.ui_controller import MusicUIConfig, MusicUIController, VideoUIConfig, VideoUIController


def _local_tmp(name: str) -> Path:
    root = Path(".pytest-local-tmp") / "textual-ui"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{name}-{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _fake_movie_page(*_args, **_kwargs) -> ui_services.UICandidatePage:
    return ui_services.UICandidatePage(
        candidates=[
            ui_services.UICandidate(
                title="Movie",
                year=2001,
                source="Wikidata",
                confidence=1.0,
                metadata={"qid": "Q1", "title": "Movie", "year": 2001},
            )
        ],
        raw_results=[],
        next_offset=0,
        has_more=False,
    )


async def _wait_for_widget(app: PlexifyTextualApp, pilot, screen_name: str, selector: str, widget_type: type) -> object:
    for _ in range(30):
        await pilot.pause()
        if app.screen.__class__.__name__ != screen_name:
            continue
        try:
            return app.screen.query_one(selector, widget_type)
        except NoMatches:
            continue
    raise AssertionError(f"{selector} was not available on {screen_name}")


def test_cli_help_includes_ui_command() -> None:
    runner = CliRunner()
    result = runner.invoke(cli.app, ["--help"])
    assert result.exit_code == 0
    assert "ui" in result.output


def test_video_ui_controller_scans_and_builds_preview(monkeypatch) -> None:
    monkeypatch.setattr(ui_controller, "load_movie_candidates", _fake_movie_page)

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
        assert controller.items[0].cache_context == "auto-selectable"
        assert len(preview.plans) == 1
        assert preview.unresolved_count == 0
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_video_preview_marks_unresolved_items(monkeypatch) -> None:
    def _fake_tv_page(*_args, **_kwargs) -> ui_services.UICandidatePage:
        return ui_services.UICandidatePage(
            candidates=[
                ui_services.UICandidate(
                    title="Series",
                    year=2008,
                    source="TVMaze",
                    confidence=1.0,
                    metadata={"id": 1, "name": "Series", "year": 2008},
                )
            ],
            raw_results=[],
            next_offset=0,
            has_more=False,
        )

    monkeypatch.setattr(ui_controller, "load_tv_candidates", _fake_tv_page)
    monkeypatch.setattr(ui_controller, "load_movie_candidates", _fake_movie_page)

    workspace = _local_tmp("video-unresolved")
    try:
        incoming = workspace / "incoming" / "Series"
        library = workspace / "library"
        incoming.mkdir(parents=True)
        library.mkdir()
        (incoming / "Pilot.mkv").write_text("x", encoding="utf-8")

        controller = VideoUIController(VideoUIConfig(incoming=workspace / "incoming", library=library))
        controller.scan()
        controller.switch_media_type(0, "tv")
        controller.accept_candidate(0, 0)
        preview = controller.build_preview()

        assert preview.unresolved_count == 1
        assert not preview.can_apply
        assert "missing season or episode" in preview.unresolved_items[0]
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_music_ui_controller_reuses_cached_decision(monkeypatch) -> None:
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
    monkeypatch.setattr(musicbrainz, "is_available", lambda: True)
    monkeypatch.setattr(musicbrainz, "fetch_release_tracks", lambda *_args, **_kwargs: [musicbrainz.Track(number=1, title="Song One", disc=1)])

    workspace = _local_tmp("music-controller")
    try:
        source = workspace / "source"
        library = workspace / "library"
        album_dir = source / "Artist" / "Album"
        album_dir.mkdir(parents=True)
        library.mkdir()
        (album_dir / "01 - Song One.flac").write_text("x", encoding="utf-8")

        albums, _errors = music_util.discover_albums(source, ["flac"])
        cache_key = music_util.album_decision_cache_key(albums[0])
        cache = library / ".plexify" / "cache.json"
        cache.parent.mkdir(parents=True)
        cache.write_text(
            f'{{"music":{{"{cache_key}":{{"decision":"selected","chosen_mbid":"mb1","reason":"cached release"}}}}}}',
            encoding="utf-8",
        )

        controller = MusicUIController(MusicUIConfig(source=source, library=library))
        controller.scan()

        assert len(controller.albums) == 1
        assert controller.albums[0].cached_decision == "selected"
        assert controller.albums[0].selected_candidate_index == 0
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_music_ui_controller_reuses_cached_skip_without_search(monkeypatch) -> None:
    monkeypatch.setattr(musicbrainz, "is_available", lambda: True)
    monkeypatch.setattr(
        musicbrainz,
        "search_releases",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("search should be skipped")),
    )

    workspace = _local_tmp("music-cached-skip")
    try:
        source = workspace / "source"
        library = workspace / "library"
        album_dir = source / "Artist" / "Album"
        album_dir.mkdir(parents=True)
        library.mkdir()
        (album_dir / "01 - Song One.flac").write_text("x", encoding="utf-8")

        albums, _errors = music_util.discover_albums(source, ["flac"])
        cache_key = music_util.album_decision_cache_key(albums[0])
        cache = library / ".plexify" / "cache.json"
        cache.parent.mkdir(parents=True)
        cache.write_text(
            f'{{"music":{{"{cache_key}":{{"decision":"skip_album","reason":"cached skip"}}}}}}',
            encoding="utf-8",
        )

        controller = MusicUIController(MusicUIConfig(source=source, library=library))
        controller.scan()

        assert len(controller.albums) == 1
        assert controller.albums[0].decision == "skip_album"
        assert controller.albums[0].status_label == "skipped"
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_music_ui_controller_reuses_filename_fallback_when_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(musicbrainz, "is_available", lambda: False)

    workspace = _local_tmp("music-unavailable-fallback")
    try:
        source = workspace / "source"
        library = workspace / "library"
        album_dir = source / "Artist" / "Album"
        album_dir.mkdir(parents=True)
        library.mkdir()
        (album_dir / "01 - Song One.flac").write_text("x", encoding="utf-8")

        albums, _errors = music_util.discover_albums(source, ["flac"])
        cache_key = music_util.album_decision_cache_key(albums[0])
        cache = library / ".plexify" / "cache.json"
        cache.parent.mkdir(parents=True)
        cache.write_text(
            f'{{"music":{{"{cache_key}":{{"decision":"filename_fallback","reason":"cached filename fallback"}}}}}}',
            encoding="utf-8",
        )

        controller = MusicUIController(MusicUIConfig(source=source, library=library))
        controller.scan()

        assert len(controller.albums) == 1
        assert controller.albums[0].decision == "filename_fallback"
        assert controller.albums[0].cached_reason == "cached filename fallback"
        assert "unavailable" in (controller.albums[0].warning or "").lower()
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_music_preview_marks_track_mapping_failure_unresolved(monkeypatch) -> None:
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
                track_count=2,
            )
        ],
    )
    monkeypatch.setattr(
        musicbrainz,
        "fetch_release_tracks",
        lambda *_args, **_kwargs: [
            musicbrainz.Track(number=1, title="Song One", disc=1),
            musicbrainz.Track(number=2, title="Song Two", disc=1),
        ],
    )
    monkeypatch.setattr(musicbrainz, "is_available", lambda: True)

    workspace = _local_tmp("music-mapping-failure")
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

        assert preview.unresolved_count == 1
        assert "track mapping failed" in preview.unresolved_items[0]
        assert not preview.can_apply
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_textual_video_flow_to_result(monkeypatch) -> None:
    monkeypatch.setattr(ui_controller, "load_movie_candidates", _fake_movie_page)

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
                review = await _wait_for_widget(app, pilot, "ReviewScreen", "#accept", Button)
                review = app.screen
                review.query_one("#accept", Button).press()
                review.query_one("#preview", Button).press()
                await _wait_for_widget(app, pilot, "PreviewScreen", "#apply", Button)
                app.screen.query_one("#apply", Button).press()
                await _wait_for_widget(app, pilot, "ResultScreen", "#result-summary", Static)
                assert app.screen.__class__.__name__ == "ResultScreen"

        asyncio.run(_run())
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_textual_apply_mode_uses_confirmation(monkeypatch) -> None:
    monkeypatch.setattr(ui_controller, "load_movie_candidates", _fake_movie_page)

    workspace = _local_tmp("video-confirm")
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
                screen.query_one("#apply-mode", Checkbox).value = True
                screen.query_one("#scan", Button).press()
                await _wait_for_widget(app, pilot, "ReviewScreen", "#accept", Button)
                review = app.screen
                review.query_one("#accept", Button).press()
                review.query_one("#preview", Button).press()
                await _wait_for_widget(app, pilot, "PreviewScreen", "#apply", Button)
                app.screen.query_one("#apply", Button).press()
                await _wait_for_widget(app, pilot, "ConfirmApplyScreen", "#confirm-apply", Button)
                assert app.screen.__class__.__name__ == "ConfirmApplyScreen"
                app.screen.query_one("#confirm-apply", Button).press()
                await _wait_for_widget(app, pilot, "ResultScreen", "#result-summary", Static)
                assert app.screen.__class__.__name__ == "ResultScreen"

        asyncio.run(_run())
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_textual_invalid_config_shows_error() -> None:
    workspace = _local_tmp("invalid-config")

    async def _run() -> None:
        app = PlexifyTextualApp()
        async with app.run_test() as pilot:
            app.push_screen(ConfigScreen("video"))
            await pilot.pause()
            screen = app.screen
            screen.query_one("#path-one", Input).value = str(workspace / "missing")
            screen.query_one("#path-two", Input).value = str(workspace / "library")
            screen.query_one("#scan", Button).press()
            await pilot.pause()
            assert "must exist" in str(screen.query_one("#config-error", Static).renderable)

    try:
        asyncio.run(_run())
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_textual_unresolved_preview_disables_apply(monkeypatch) -> None:
    def _fake_tv_page(*_args, **_kwargs) -> ui_services.UICandidatePage:
        return ui_services.UICandidatePage(
            candidates=[
                ui_services.UICandidate(
                    title="Series",
                    year=2008,
                    source="TVMaze",
                    confidence=1.0,
                    metadata={"id": 1, "name": "Series", "year": 2008, "manual": False},
                )
            ],
            raw_results=[],
            next_offset=0,
            has_more=False,
        )

    monkeypatch.setattr(ui_controller, "load_tv_candidates", _fake_tv_page)
    monkeypatch.setattr(ui_controller, "load_movie_candidates", _fake_movie_page)

    workspace = _local_tmp("video-preview-gate")
    try:
        incoming = workspace / "incoming" / "Series"
        library = workspace / "library"
        incoming.mkdir(parents=True)
        library.mkdir()
        (incoming / "Pilot.mkv").write_text("x", encoding="utf-8")

        async def _run() -> None:
            app = PlexifyTextualApp()
            async with app.run_test() as pilot:
                app.push_screen(ConfigScreen("video"))
                await pilot.pause()
                screen = app.screen
                screen.query_one("#path-one", Input).value = str(workspace / "incoming")
                screen.query_one("#path-two", Input).value = str(library)
                screen.query_one("#scan", Button).press()
                await _wait_for_widget(app, pilot, "ReviewScreen", "#switch", Button)
                review = app.screen
                review.query_one("#switch", Button).press()
                await pilot.pause()
                review.query_one("#accept", Button).press()
                review.query_one("#preview", Button).press()
                await _wait_for_widget(app, pilot, "PreviewScreen", "#apply", Button)
                preview = app.screen
                assert preview.query_one("#apply", Button).disabled is True
                assert "Unresolved:" in str(preview.query_one("#preview-plans", Static).renderable)

        asyncio.run(_run())
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
