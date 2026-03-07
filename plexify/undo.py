from __future__ import annotations

import shutil
from pathlib import Path

from .logging_config import get_logger
from .report import read_report

logger = get_logger(__name__)

def _is_within_root(path: Path, root: Path) -> bool:
    try:
        return path.resolve(strict=False).is_relative_to(root.resolve(strict=False))
    except (OSError, RuntimeError, ValueError):
        return False


def undo_report(path: Path, library_root: Path | None = None) -> list[str]:
    payload = read_report(path)
    copy_mode = bool(payload.get("copy"))
    errors: list[str] = []
    root = library_root.resolve(strict=False) if library_root is not None else None
    for op in payload.get("operations", []):
        src = Path(op.get("source"))
        dest = Path(op.get("destination"))
        if root is not None:
            if not dest.is_absolute():
                errors.append(f"{dest}: blocked non-absolute destination path")
                continue
            if not _is_within_root(dest, root):
                errors.append(f"{dest}: blocked path outside library root ({root})")
                continue
            # Move reports commonly restore into an incoming folder outside library root.
            if not copy_mode and not src.is_absolute():
                errors.append(f"{src}: blocked non-absolute source path")
                continue
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
        except (OSError, shutil.Error, ValueError) as exc:
            logger.exception("undo_operation_failed", extra={"source": src, "destination": dest})
            errors.append(f"{dest}: {exc}")
    return errors
