from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .util import MovePlan, json_dump


class ReportFormatError(ValueError):
    pass


def write_report(path: Path, plans: list[MovePlan], mode: str, copy_mode: bool) -> None:
    payload: dict[str, Any] = {
        "mode": mode,
        "copy": copy_mode,
        "operations": [
            {
                "source": str(plan.source),
                "destination": str(plan.destination),
                "media_type": plan.media_type,
                "metadata": plan.metadata,
            }
            for plan in plans
        ],
    }
    json_dump(path, payload)


def read_report(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ReportFormatError(f"Unable to read report: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ReportFormatError(f"Invalid JSON in report: {path}") from exc

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
