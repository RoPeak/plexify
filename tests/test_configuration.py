from pathlib import Path

import pytest
from typer.testing import CliRunner

from plexify import cli
from plexify.configuration import ConfigurationError, config_path, format_effective_config, load_config, state_paths


def test_config_uses_xdg_and_config_file_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    path = config_path()
    path.parent.mkdir(parents=True)
    path.write_text(
        '[defaults]\npublication = "copy"\nrequire_same_filesystem = true\nallow_risky_enter_accept = true\n'
        '[movies]\nincoming = "/incoming/movies"\nlibrary = "/library/Movies"\n'
        '[tv]\nincoming = "/incoming/tv"\nlibrary = "/library/TV Shows"\n',
        encoding="utf-8",
    )
    config = load_config()
    assert config.movies.incoming == Path("/incoming/movies")
    assert config.movies.library == Path("/library/Movies")
    assert config.tv.incoming == Path("/incoming/tv")
    assert config.tv.library == Path("/library/TV Shows")
    assert config.publication == "copy"
    assert config.require_same_filesystem
    assert "TV Shows" in format_effective_config(config)


def test_config_relative_paths_are_relative_to_config(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[movies]\nincoming = "incoming"\nlibrary = "Movies"\n', encoding="utf-8")
    config = load_config(path)
    assert config.movies.incoming == tmp_path / "incoming"
    assert config.movies.library == tmp_path / "Movies"
    assert config.tv.incoming is None
    assert config.mode == "dry-run"
    assert config.publication == "copy"


def test_missing_config_uses_safe_defaults(tmp_path: Path) -> None:
    config = load_config(tmp_path / "missing.toml")
    assert config.mode == "dry-run"
    assert config.publication == "copy"
    assert config.on_conflict == "rename"
    assert config.require_same_filesystem is False


@pytest.mark.parametrize("contents", ["[defaults\n", '[defaults]\nmode = "danger"\n', '[defaults]\nuse_cache = "yes"\n'])
def test_malformed_or_invalid_config_fails_clearly(tmp_path: Path, contents: str) -> None:
    path = tmp_path / "bad.toml"
    path.write_text(contents, encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_config(path)


def test_state_paths_are_xdg_and_library_scoped(tmp_path: Path) -> None:
    library = tmp_path / "library"
    cache, reports = state_paths(library, {
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
    })
    assert cache == tmp_path / "cache" / "video-ingest" / cache.parent.name / "cache.json"
    assert reports == tmp_path / "state" / "video-ingest" / cache.parent.name / "reports"
    assert state_paths(tmp_path / "other", {"XDG_CACHE_HOME": str(tmp_path), "XDG_STATE_HOME": str(tmp_path)})[0] != cache


def test_config_command_displays_effective_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[movies]\nincoming = "/incoming"\nlibrary = "/Movies"\n', encoding="utf-8")
    result = CliRunner().invoke(cli.app, ["config", "--file", str(path)])
    assert result.exit_code == 0
    assert "/incoming" in result.output
    assert "Publication: copy" in result.output


def test_entrypoint_preserves_plexify_and_adds_video_ingest() -> None:
    import tomllib
    from pathlib import Path

    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    scripts = project["project"]["scripts"]
    assert scripts["video-ingest"] == "plexify.cli:video_ingest"
    assert scripts["plexify"] == "plexify.cli:app"


def test_video_ingest_entrypoint_routes_bare_invocation_to_guided_wizard(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[str] = []
    monkeypatch.setattr(cli.sys, "argv", ["video-ingest"])
    monkeypatch.setattr(cli, "_video_ingest_wizard", lambda: called.append("wizard"))
    cli.video_ingest()
    assert called == ["wizard"]


def test_cli_paths_and_flags_override_configuration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[defaults]\nmode = "dry-run"\npublication = "copy"\n'
        '[movies]\nincoming = "/configured/in"\nlibrary = "/configured/Movies"\n',
        encoding="utf-8",
    )
    captured: list[cli.OrganiseOptions] = []
    monkeypatch.setattr(cli, "run_organise", captured.append)
    incoming = tmp_path / "explicit-in"
    library = tmp_path / "explicit-library"
    result = CliRunner().invoke(
        cli.app,
        ["organise", "--config", str(config_path), "--incoming", str(incoming), "--library", str(library),
         "--mode", "apply", "--move", "--media-type", "movie"],
    )
    assert result.exit_code == 0, result.output
    assert captured[0].incoming == incoming
    assert captured[0].library == library
    assert captured[0].mode == "apply"
    assert captured[0].copy_mode is False


def test_configured_movie_paths_flow_into_organise(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = tmp_path / "config.toml"
    incoming = tmp_path / "ingest"
    library = tmp_path / "Movies"
    config_path.write_text(
        '[defaults]\nmode = "dry-run"\npublication = "copy"\nrequire_same_filesystem = true\nallow_risky_enter_accept = true\n'
        f'[movies]\nincoming = "{incoming}"\nlibrary = "{library}"\n',
        encoding="utf-8",
    )
    captured: list[cli.OrganiseOptions] = []
    monkeypatch.setattr(cli, "run_organise", captured.append)
    result = CliRunner().invoke(cli.app, ["organise", "--config", str(config_path), "--media-type", "movie"])
    assert result.exit_code == 0, result.output
    options = captured[0]
    assert options.incoming == incoming
    assert options.library == library
    assert options.copy_mode is True
    assert options.require_same_filesystem is True
    assert options.allow_risky_enter_accept is True
    assert options.category_root is True
