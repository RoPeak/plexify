from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .util import MovePlan, ensure_dir


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
    ensure_dir(path.parent)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def read_report(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
