from typer.testing import CliRunner

from plexify.cli import app


def test_cli_help():
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "organise" in result.output


def test_organise_help():
    runner = CliRunner()
    result = runner.invoke(app, ["organise", "--help"])
    assert result.exit_code == 0


def test_wizard_help():
    runner = CliRunner()
    result = runner.invoke(app, ["wizard", "--help"])
    assert result.exit_code == 0
