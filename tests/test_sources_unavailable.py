from pathlib import Path

import requests
from typer.testing import CliRunner

from plexify import cli
from plexify.sources import tvmaze, wikidata


def test_sources_unavailable_does_not_crash(monkeypatch, tmp_path: Path) -> None:
    def _raise(*_args, **_kwargs):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(requests.Session, "get", _raise)
    wikidata._reset_state()
    tvmaze._reset_state()

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


def test_movie_candidates_report_wikidata_unavailable(monkeypatch, tmp_path: Path) -> None:
    messages: list[str] = []

    def _raise(*_args, **_kwargs):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(requests.Session, "get", _raise)
    monkeypatch.setattr(cli, "_safe_print", lambda message, *_args, **_kwargs: messages.append(str(message)))
    wikidata._reset_state()

    item = cli.InferredItem(
        path=tmp_path / "Gladiator (2000).mkv",
        media_type="movie",
        title="Gladiator",
        year=2000,
        episode_title=None,
    )

    page = cli._movie_candidates(
        item,
        session=requests.Session(),
        cache=cli.Cache(tmp_path / "cache.json"),
        show_cache=False,
        interactive=False,
    )

    assert page.candidates == []
    assert any("Wikidata unavailable" in message for message in messages)


def test_tv_candidates_report_tvmaze_unavailable(monkeypatch, tmp_path: Path) -> None:
    messages: list[str] = []

    def _raise(*_args, **_kwargs):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(requests.Session, "get", _raise)
    monkeypatch.setattr(cli, "_safe_print", lambda message, *_args, **_kwargs: messages.append(str(message)))
    tvmaze._reset_state()

    incoming = tmp_path / "incoming"
    incoming.mkdir()
    path = incoming / "Show" / "Season 1" / "Show.S01E02.mkv"
    path.parent.mkdir(parents=True)
    path.write_text("x", encoding="utf-8")
    item = cli.InferredItem(path=path, media_type="tv", title="Show", year=2005, season=1, episode=2, episode_title=None)

    page = cli._tv_candidates(
        item,
        session=requests.Session(),
        cache=cli.Cache(tmp_path / "cache.json"),
        show_cache=False,
        incoming_root=incoming,
        interactive=False,
    )

    assert page.candidates == []
    assert any("TVMaze unavailable" in message for message in messages)


def test_wikidata_availability_recovers_after_cooldown(monkeypatch) -> None:
    wikidata._reset_state()
    wikidata._set_unavailable("offline", cooldown=1.0)
    monkeypatch.setattr(wikidata.time, "monotonic", lambda: 100.0)
    wikidata._state.recover_at = 10.0

    assert wikidata.is_available() is True
    assert wikidata.unavailable_reason() is None


def test_tvmaze_warning_is_deduplicated(monkeypatch) -> None:
    messages: list[str] = []
    tvmaze._reset_state()
    monkeypatch.setattr(tvmaze.logger, "warning", lambda message, *args, **kwargs: messages.append(str(message)))

    tvmaze._set_unavailable("offline")
    tvmaze._set_unavailable("offline again")

    assert messages == ["offline"]
