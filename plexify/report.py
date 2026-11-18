from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .models import ReportPayload
from .provenance import machine_instance_id
from .util import MovePlan, ensure_dir, json_dump


class ReportFormatError(ValueError):
    pass


class ReportStream:
    def __init__(self, path: Path, mode: str, copy_mode: bool) -> None:
        self.path = path
        self.mode = mode
        self.copy_mode = copy_mode
        ensure_dir(path.parent)
        if path.exists() or path.is_symlink():
            raise FileExistsError(f"Refusing to overwrite existing report: {path}")
        self._temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.partial")
        self._handle = self._temporary.open("x", encoding="utf-8", newline="\n")
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
        self._write_line(
            {
                "type": "header",
                "report_schema": "video-ingest-publication-v4",
                "mode": self.mode,
                "copy": self.copy_mode,
                "version": 4,
                "producer": "video-ingest",
                "producer_version": __version__,
                "producer_instance_id": machine_instance_id(),
                "started_at": datetime.now(timezone.utc).isoformat(),
            }
        )
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
                "action": "published",
                "source_lifecycle": "preserved" if self.copy_mode else "removed",
                "verification": "destination-created",
            }
        )
        self._operations += 1

    def append_verified_existing(self, plan: MovePlan) -> None:
        """Record an approved SHA-256-identical existing library target.

        This is provenance only: no destination was created and cleanup must
        still independently compare current source and destination hashes.
        """
        if self._closed:
            return
        self._write_line(
            {
                "type": "operation",
                "source": str(plan.source.resolve(strict=False)),
                "destination": str(plan.destination.resolve(strict=False)),
                "media_type": plan.media_type,
                "metadata": plan.metadata,
                "action": "verified-existing-duplicate",
                "source_lifecycle": "preserved",
                "verification": "sha256",
                "source_sha256": plan.metadata.get("source_sha256"),
                "destination_sha256": plan.metadata.get("destination_sha256"),
            }
        )
        self._operations += 1

    def finalize(self, *, status: str = "completed") -> None:
        if self._closed:
            return
        if status not in {"completed", "failed", "cancelled"}:
            raise ValueError(f"unsupported report status: {status}")
        self._write_line({"type": "final", "operations": self._operations, "status": status})
        os.fsync(self._handle.fileno())
        self._handle.close()
        self._closed = True
        # A hard-link publication is atomic and fails if another process has
        # claimed the final name. Both paths are in the same directory, so it
        # cannot cross filesystems. Preserve the completed hidden report if a
        # collision occurs so successful operations do not lose provenance.
        os.link(self._temporary, self.path)
        self._temporary.unlink()
        if hasattr(os, "O_DIRECTORY"):
            directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)

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


def _validate_payload(payload: dict[str, Any]) -> ReportPayload:
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
    return payload  # type: ignore[return-value]


def _parse_jsonl_report(text: str) -> ReportPayload:
    mode: str | None = None
    copy_mode = False
    operations: list[dict[str, Any]] = []
    header_seen = False
    version = 1
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
            row_version = row.get("version", 1)
            if row_version not in {1, 2, 3, 4}:
                raise ReportFormatError("Report header has unsupported version.")
            mode = row_mode
            copy_mode = row_copy
            version = row_version
            continue
        if row_type == "operation":
            operations.append(
                {
                    "source": row.get("source"),
                    "destination": row.get("destination"),
                    "media_type": row.get("media_type"),
                    "metadata": row.get("metadata"),
                    "action": row.get("action") if version >= 2 else "published",
                    "source_lifecycle": row.get("source_lifecycle") if version >= 3 else None,
                    "verification": row.get("verification") if version >= 3 else None,
                    "source_sha256": row.get("source_sha256") if version >= 3 else None,
                    "destination_sha256": row.get("destination_sha256") if version >= 3 else None,
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


def read_report(path: Path) -> ReportPayload:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ReportFormatError(f"Unable to read report: {path}") from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = _parse_jsonl_report(text)
    return _validate_payload(payload)
