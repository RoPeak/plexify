from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
import typer

from plexify import cli
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


def test_configure_logging_json_with_file(monkeypatch) -> None:
    log_path = Path("test-logs") / "plexify.log"
    captured: dict[str, object] = {}

    class DummyFileHandler(logging.Handler):
        def __init__(self, filename, encoding=None):
            super().__init__()
            captured["filename"] = str(filename)

        def emit(self, record: logging.LogRecord) -> None:
            captured["payload"] = self.format(record)

    monkeypatch.setattr(logging, "FileHandler", DummyFileHandler)
    configure_logging(level="INFO", fmt="json", log_file=log_path)
    logger = get_logger("tests.logging")

    log_event(logger, "cache_hit", cache_scope="tv", cache_key="k1")

    assert captured["filename"].endswith(str(log_path))
    payload = json.loads(str(captured["payload"]))
    assert payload["event"] == "cache_hit"
    assert payload["cache_scope"] == "tv"


def test_configure_logging_creates_parent_directory(monkeypatch) -> None:
    log_path = Path(".plexify") / "run.log"
    mkdir_calls: list[Path] = []
    original_mkdir = Path.mkdir

    def _fake_mkdir(self, *args, **kwargs):
        mkdir_calls.append(self)
        return None

    class DummyFileHandler(logging.Handler):
        def __init__(self, *_args, **_kwargs):
            super().__init__()

        def emit(self, _record: logging.LogRecord) -> None:
            return None

    monkeypatch.setattr(Path, "mkdir", _fake_mkdir)
    monkeypatch.setattr(logging, "FileHandler", DummyFileHandler)
    configure_logging(level="INFO", fmt="text", log_file=log_path)
    logger = get_logger("tests.logging.dir")
    log_event(logger, "run_started", command="wizard")

    assert log_path.parent in mkdir_calls
    monkeypatch.setattr(Path, "mkdir", original_mkdir)


def test_initialise_logging_rejects_invalid_log_level() -> None:
    with pytest.raises(typer.Exit):
        cli._initialise_logging("VERBOSE", "text", None)
