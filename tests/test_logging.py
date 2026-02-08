from __future__ import annotations

import json
import logging
from pathlib import Path

from typer.testing import CliRunner

from plexify.cli import app
from plexify.logging_config import JsonFormatter, configure_logging, get_logger, log_event


def test_json_formatter_emits_event_fields() -> None:
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="plexify.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="run_started",
        args=(),
        exc_info=None,
    )
    record.event = "run_started"
    record.run_id = "abc123"
    record.command = "organise"

    payload = json.loads(formatter.format(record))
    assert payload["event"] == "run_started"
    assert payload["run_id"] == "abc123"
    assert payload["command"] == "organise"
    assert payload["message"] == "run_started"


def test_configure_logging_json_with_file(tmp_path: Path) -> None:
    log_path = tmp_path / "plexify.log"
    configure_logging(level="INFO", fmt="json", log_file=log_path)
    logger = get_logger("tests.logging")

    log_event(logger, "cache_hit", cache_scope="tv", cache_key="k1")

    content = log_path.read_text(encoding="utf-8").strip()
    assert content
    payload = json.loads(content)
    assert payload["event"] == "cache_hit"
    assert payload["cache_scope"] == "tv"


def test_organise_rejects_invalid_log_level(tmp_path: Path) -> None:
    incoming = tmp_path / "incoming"
    library = tmp_path / "library"
    incoming.mkdir()
    library.mkdir()
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "organise",
            "--incoming",
            str(incoming),
            "--library",
            str(library),
            "--log-level",
            "VERBOSE",
        ],
    )

    assert result.exit_code == 2
    assert "Invalid log level" in result.output
