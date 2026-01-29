from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .util import json_dump, json_load


@dataclass
class CacheEntry:
    key: str
    value: dict[str, Any]


class Cache:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.data = json_load(path)
        self.data.setdefault("shows", {})
        self.data.setdefault("movies", {})
        self.data.setdefault("enrichment", {})

    def get_show(self, key: str) -> dict[str, Any] | None:
        return self.data.get("shows", {}).get(key)

    def set_show(self, key: str, value: dict[str, Any]) -> None:
        self.data.setdefault("shows", {})[key] = value

    def get_movie(self, key: str) -> dict[str, Any] | None:
        return self.data.get("movies", {}).get(key)

    def set_movie(self, key: str, value: dict[str, Any]) -> None:
        self.data.setdefault("movies", {})[key] = value

    def save(self) -> None:
        json_dump(self.path, self.data)

    def get_enrichment(self, key: str) -> dict[str, Any] | None:
        return self.data.get("enrichment", {}).get(key)

    def set_enrichment(self, key: str, value: dict[str, Any]) -> None:
        self.data.setdefault("enrichment", {})[key] = value
