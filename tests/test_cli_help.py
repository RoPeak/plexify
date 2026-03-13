from typer.testing import CliRunner

from plexify.cli import app


def test_cli_help():
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "organise" in result.output
    assert "music" in result.output
    assert "cache" in result.output


def test_organise_help():
    runner = CliRunner()
    result = runner.invoke(app, ["organise", "--help"])
    assert result.exit_code == 0
    assert "--log-level" in result.output
    assert "--log-format" in result.output
    assert "--log-file" in result.output
    assert "--offline" in result.output
    assert "--quiet" in result.output
    assert "--prune-ignore" in result.output
    assert "Auto-accept unambiguous top" in result.output
    assert "result when confidence >=" in result.output
    assert "Minimum confidence for" in result.output
    assert "unambiguous auto acceptance" in result.output


def test_cache_help():
    runner = CliRunner()
    result = runner.invoke(app, ["cache", "--help"])
    assert result.exit_code == 0
    assert "stats" in result.output
    assert "prune" in result.output
    assert "delete" in result.output


def test_wizard_help():
    runner = CliRunner()
    result = runner.invoke(app, ["wizard", "--help"])
    assert result.exit_code == 0
    assert "--log-level" in result.output
    assert "--log-format" in result.output
    assert "--log-file" in result.output


def test_music_help():
    runner = CliRunner()
    result = runner.invoke(app, ["music", "--help"])
    assert result.exit_code == 0
    assert "--log-level" in result.output
    assert "--log-format" in result.output
    assert "--log-file" in result.output
    assert "--offline" in result.output
    assert "--cleanup-unknown" in result.output
    assert "Confirmation token" in result.output


def test_default_callback_invokes_wizard_with_parsed_defaults(monkeypatch) -> None:
    runner = CliRunner()
    called: dict[str, object] = {}

    def _fake_wizard(*, log_level: str, log_format: str, log_file):
        called["log_level"] = log_level
        called["log_format"] = log_format
        called["log_file"] = log_file

    monkeypatch.setattr("plexify.cli.wizard", _fake_wizard)
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert called["log_level"] == "WARNING"
    assert called["log_format"] == "text"
    assert called["log_file"] is None
