from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .util import json_dump, json_load

CACHE_SCHEMA_VERSION = 2
MIN_SUPPORTED_SCHEMA_VERSION = 1


@dataclass
class CacheEntry:
    key: str
    value: dict[str, Any]


def _upgrade_cache_data(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        data = {}
    schema_version = data.get("schema_version")
    if not isinstance(schema_version, int):
        schema_version = 1
    if schema_version < MIN_SUPPORTED_SCHEMA_VERSION:
        schema_version = MIN_SUPPORTED_SCHEMA_VERSION
    upgraded = dict(data)
    upgraded.setdefault("shows", {})
    upgraded.setdefault("movies", {})
    upgraded.setdefault("enrichment", {})
    upgraded["schema_version"] = CACHE_SCHEMA_VERSION
    return upgraded


class Cache:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.data = _upgrade_cache_data(json_load(path))

    def get_show(self, key: str) -> dict[str, Any] | None:
        return self.data.get("shows", {}).get(key)

    def set_show(self, key: str, value: dict[str, Any]) -> None:
        self.data.setdefault("shows", {})[key] = value

    def get_movie(self, key: str) -> dict[str, Any] | None:
        return self.data.get("movies", {}).get(key)

    def set_movie(self, key: str, value: dict[str, Any]) -> None:
        self.data.setdefault("movies", {})[key] = value

    def delete_show(self, key: str) -> None:
        self.data.setdefault("shows", {}).pop(key, None)

    def delete_movie(self, key: str) -> None:
        self.data.setdefault("movies", {}).pop(key, None)

    def save(self) -> None:
        json_dump(self.path, self.data)

    def get_enrichment(self, key: str) -> dict[str, Any] | None:
        return self.data.get("enrichment", {}).get(key)

    def set_enrichment(self, key: str, value: dict[str, Any]) -> None:
        self.data.setdefault("enrichment", {})[key] = value


class NullCache(Cache):
    def __init__(self) -> None:
        self.path = Path(".")
        self.data = {
            "schema_version": CACHE_SCHEMA_VERSION,
            "shows": {},
            "movies": {},
            "enrichment": {},
        }

    def get_show(self, key: str) -> dict[str, Any] | None:
        return None

    def set_show(self, key: str, value: dict[str, Any]) -> None:
        return None

    def get_movie(self, key: str) -> dict[str, Any] | None:
        return None

    def set_movie(self, key: str, value: dict[str, Any]) -> None:
        return None

    def delete_show(self, key: str) -> None:
        return None

    def delete_movie(self, key: str) -> None:
        return None

    def save(self) -> None:
        return None

    def get_enrichment(self, key: str) -> dict[str, Any] | None:
        return None

    def set_enrichment(self, key: str, value: dict[str, Any]) -> None:
        return None
