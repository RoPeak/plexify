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


def test_log_event_candidate_selected_schema_fields() -> None:
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="plexify.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="candidate_selected",
        args=(),
        exc_info=None,
    )
    record.event = "candidate_selected"
    record.media_type = "movie"
    record.selection_mode = "confirmed"
    record.selection_source = "Wikidata"
    record.decision_reason = "user_or_auto_selection"
    record.path = "C:/incoming/file.mkv"
    record.query = "file"
    record.confidence = 0.95
    record.cache_scope = "movie"

    payload = json.loads(formatter.format(record))
    assert payload["event"] == "candidate_selected"
    assert payload["media_type"] == "movie"
    assert payload["selection_mode"] == "confirmed"
    assert payload["selection_source"] == "Wikidata"
    assert payload["decision_reason"] == "user_or_auto_selection"
    assert payload["cache_scope"] == "movie"


def test_log_event_run_finished_schema_fields() -> None:
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="plexify.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="run_finished",
        args=(),
        exc_info=None,
    )
    record.event = "run_finished"
    record.run_id = "abc"
    record.command = "organise"
    record.status = "success"
    record.planned_count = 10
    record.skipped_count = 2
    record.error_count = 0
    record.elapsed_seconds = 1.23
    record.applied = True

    payload = json.loads(formatter.format(record))
    assert payload["event"] == "run_finished"
    assert payload["planned_count"] == 10
    assert payload["skipped_count"] == 2
    assert payload["error_count"] == 0
    assert payload["elapsed_seconds"] == 1.23
    assert payload["applied"] is True
