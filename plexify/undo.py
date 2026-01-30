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
                if not dest.exists():
                    errors.append(f"{dest}: missing destination to remove")
                    continue
                dest.unlink()
            else:
                if not dest.exists():
                    errors.append(f"{dest}: missing destination to restore")
                    continue
                if src.exists():
                    errors.append(f"{src}: source already exists")
                    continue
                src.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(dest, src)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{dest}: {exc}")
    return errors
