from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

WINDOWS_INVALID = r'<>"/\\|?*'


def sanitise_name(value: str) -> str:
    if not value:
        return "Unknown"
    cleaned = value.replace(":", " - ")
    cleaned = re.sub(r"[\\/]+", " ", cleaned)
    cleaned = "".join(" " if ch in WINDOWS_INVALID else ch for ch in cleaned)
    cleaned = re.sub(r"_\s+", "_", cleaned)
    cleaned = re.sub(r"\s+_", "_", cleaned)
    cleaned = cleaned.rstrip(". ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or "Unknown"


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def json_load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def json_dump(path: Path, data: dict[str, Any]) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def now_timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def iter_video_files(root: Path, extensions: Iterable[str]) -> list[Path]:
    exts = {ext.lower().lstrip(".") for ext in extensions}
    results: list[Path] = []
    for base, _, files in os.walk(root):
        for name in files:
            if not name:
                continue
            suffix = Path(name).suffix.lower().lstrip(".")
            if suffix in exts:
                results.append(Path(base) / name)
    return results


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    counter = 2
    while True:
        candidate = path.with_name(f"{path.stem} ({counter}){path.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


@dataclass(frozen=True)
class MovePlan:
    source: Path
    destination: Path
    mode: str
    media_type: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class ExecutionResult:
    moved: list[MovePlan]
    skipped: list[MovePlan]
    errors: list[str]
