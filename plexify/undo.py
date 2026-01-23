from __future__ import annotations

import shutil
from pathlib import Path

from .report import read_report


def undo_report(path: Path) -> list[str]:
    payload = read_report(path)
    copy_mode = bool(payload.get("copy"))
    errors: list[str] = []
    for op in payload.get("operations", []):
        src = Path(op.get("source"))
        dest = Path(op.get("destination"))
        try:
            if copy_mode:
                if dest.exists():
                    dest.unlink()
            else:
                if dest.exists():
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(dest, src)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{dest}: {exc}")
    return errors
