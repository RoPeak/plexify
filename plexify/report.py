from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .util import MovePlan, ensure_dir, json_dump


class ReportFormatError(ValueError):
    pass


class ReportStream:
    def __init__(self, path: Path, mode: str, copy_mode: bool) -> None:
        self.path = path
        self.mode = mode
        self.copy_mode = copy_mode
        ensure_dir(path.parent)
        self._handle = path.open("w", encoding="utf-8", newline="\n")
        self._operations = 0
        self._header_written = False
        self._closed = False
        self._write_header()

    def _write_line(self, payload: dict[str, Any]) -> None:
        self._handle.write(json.dumps(payload, ensure_ascii=True) + "\n")
        self._handle.flush()

    def _write_header(self) -> None:
        if self._header_written:
            return
        self._write_line({"type": "header", "mode": self.mode, "copy": self.copy_mode, "version": 1})
        self._header_written = True

    def append(self, plan: MovePlan) -> None:
        if self._closed:
            return
        self._write_line(
            {
                "type": "operation",
                "source": str(plan.source.resolve(strict=False)),
                "destination": str(plan.destination.resolve(strict=False)),
                "media_type": plan.media_type,
                "metadata": plan.metadata,
            }
        )
        self._operations += 1

    def finalize(self) -> None:
        if self._closed:
            return
        self._write_line({"type": "final", "operations": self._operations})

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._handle.close()


def open_report_stream(path: Path, mode: str, copy_mode: bool) -> ReportStream:
    return ReportStream(path=path, mode=mode, copy_mode=copy_mode)


def write_report(path: Path, plans: list[MovePlan], mode: str, copy_mode: bool) -> None:
    payload: dict[str, Any] = {
        "mode": mode,
        "copy": copy_mode,
        "operations": [
            {
                "source": str(plan.source.resolve(strict=False)),
                "destination": str(plan.destination.resolve(strict=False)),
                "media_type": plan.media_type,
                "metadata": plan.metadata,
            }
            for plan in plans
        ],
    }
    json_dump(path, payload)


def _validate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ReportFormatError("Report root must be a JSON object.")
    operations = payload.get("operations")
    if not isinstance(operations, list):
        raise ReportFormatError("Report must include an 'operations' array.")
    for idx, operation in enumerate(operations, start=1):
        if not isinstance(operation, dict):
            raise ReportFormatError(f"Operation #{idx} must be an object.")
        source = operation.get("source")
        destination = operation.get("destination")
        if not isinstance(source, str) or not source.strip():
            raise ReportFormatError(f"Operation #{idx} has invalid source path.")
        if not isinstance(destination, str) or not destination.strip():
            raise ReportFormatError(f"Operation #{idx} has invalid destination path.")
    return payload


def _parse_jsonl_report(text: str) -> dict[str, Any]:
    mode: str | None = None
    copy_mode = False
    operations: list[dict[str, Any]] = []
    header_seen = False
    for idx, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            row = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ReportFormatError(f"Invalid JSONL in report at line {idx}.") from exc
        if not isinstance(row, dict):
            raise ReportFormatError(f"Invalid JSONL record at line {idx}.")
        row_type = row.get("type")
        if row_type == "header":
            if header_seen:
                raise ReportFormatError("Report contains multiple headers.")
            header_seen = True
            row_mode = row.get("mode")
            row_copy = row.get("copy")
            if not isinstance(row_mode, str) or not row_mode.strip():
                raise ReportFormatError("Report header has invalid mode.")
            if not isinstance(row_copy, bool):
                raise ReportFormatError("Report header has invalid copy flag.")
            mode = row_mode
            copy_mode = row_copy
            continue
        if row_type == "operation":
            operations.append(
                {
                    "source": row.get("source"),
                    "destination": row.get("destination"),
                    "media_type": row.get("media_type"),
                    "metadata": row.get("metadata"),
                }
            )
            continue
        if row_type == "final":
            continue
        raise ReportFormatError(f"Unsupported JSONL record type at line {idx}: {row_type!r}")
    if not header_seen:
        raise ReportFormatError("Report JSONL header is missing.")
    return {
        "mode": mode,
        "copy": copy_mode,
        "operations": operations,
    }


def read_report(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ReportFormatError(f"Unable to read report: {path}") from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = _parse_jsonl_report(text)
    return _validate_payload(payload)
