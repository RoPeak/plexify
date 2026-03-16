import re

from typer.testing import CliRunner

from plexify.cli import app


ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
HELP_ENV = {"TERM": "dumb", "NO_COLOR": "1", "COLUMNS": "120"}


def _normalise_help_output(output: str) -> str:
    text = ANSI_RE.sub("", output)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n+", "\n", text)
    return text.casefold()


def _invoke_help(*args: str) -> str:
    runner = CliRunner()
    result = runner.invoke(app, list(args), env=HELP_ENV)
    assert result.exit_code == 0
    return _normalise_help_output(result.output)


def test_cli_help():
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "organise" in result.output
    assert "music" in result.output
    assert "cache" in result.output


def test_organise_help():
    output = _invoke_help("organise", "--help")
    assert "log level" in output
    assert "log format" in output
    assert "log file" in output
    assert "offline" in output
    assert "quiet" in output
    assert "prune-empty-dirs" in output
    assert "ignore" in output
    assert "filename" in output
    assert "auto-accept unambiguous top" in output
    assert "result when confidence >=" in output
    assert "minimum confidence for" in output
    assert "unambiguous auto acceptance" in output


def test_cache_help():
    runner = CliRunner()
    result = runner.invoke(app, ["cache", "--help"])
    assert result.exit_code == 0
    assert "stats" in result.output
    assert "prune" in result.output
    assert "delete" in result.output


def test_wizard_help():
    output = _invoke_help("wizard", "--help")
    assert "log level" in output
    assert "log format" in output
    assert "log file" in output


def test_music_help():
    output = _invoke_help("music", "--help")
    assert "log level" in output
    assert "log format" in output
    assert "log file" in output
    assert "offline" in output
    assert "cleanup" in output
    assert "unknown" in output
    assert "confirmation token" in output


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
