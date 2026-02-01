from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.markup import escape


def rich_escape(value: Any) -> str:
    return escape(str(value))


def format_path(value: Path | str) -> str:
    return rich_escape(str(value))
