from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .logging_config import get_logger
from .util import json_dump, json_load

CACHE_SCHEMA_VERSION = 3
MIN_SUPPORTED_SCHEMA_VERSION = 1
LOCK_TIMEOUT_SECONDS = 1.0
LOCK_RETRY_DELAY_SECONDS = 0.05
logger = get_logger(__name__)


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
    upgraded.setdefault("music", {})
    upgraded["schema_version"] = CACHE_SCHEMA_VERSION
    return upgraded


class Cache:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.data = _upgrade_cache_data(json_load(path))
        self._dirty = False
        self._batch_depth = 0

    def get_show(self, key: str) -> dict[str, Any] | None:
        return self.data.get("shows", {}).get(key)

    def set_show(self, key: str, value: dict[str, Any]) -> None:
        self.data.setdefault("shows", {})[key] = value
        self._dirty = True

    def get_movie(self, key: str) -> dict[str, Any] | None:
        return self.data.get("movies", {}).get(key)

    def set_movie(self, key: str, value: dict[str, Any]) -> None:
        self.data.setdefault("movies", {})[key] = value
        self._dirty = True

    def delete_show(self, key: str) -> None:
        self.data.setdefault("shows", {}).pop(key, None)
        self._dirty = True

    def delete_movie(self, key: str) -> None:
        self.data.setdefault("movies", {}).pop(key, None)
        self._dirty = True

    def _acquire_lock(self) -> Path | None:
        lock_path = Path(str(self.path) + ".lock")
        deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
        while True:
            try:
                lock_path.parent.mkdir(parents=True, exist_ok=True)
                with lock_path.open("x", encoding="utf-8") as handle:
                    handle.write(str(time.time()))
                return lock_path
            except FileExistsError:
                if time.monotonic() >= deadline:
                    logger.warning("cache_lock_timeout", extra={"path": str(lock_path)})
                    return None
                time.sleep(LOCK_RETRY_DELAY_SECONDS)
            except OSError as exc:
                logger.warning("cache_lock_error", extra={"path": str(lock_path), "error": str(exc)})
                return None

    def save(self, force: bool = False) -> None:
        self.save_with_status(force=force)

    def save_with_status(self, force: bool = False) -> bool:
        if self._batch_depth > 0 and not force:
            return True
        if not self._dirty and not force:
            return True
        lock_path = self._acquire_lock()
        if lock_path is None:
            return False
        try:
            json_dump(self.path, self.data)
            self._dirty = False
            return True
        finally:
            try:
                lock_path.unlink(missing_ok=True)
            except OSError:
                logger.warning("cache_lock_cleanup_failed", extra={"path": str(lock_path)})

    def get_enrichment(self, key: str) -> dict[str, Any] | None:
        return self.data.get("enrichment", {}).get(key)

    def set_enrichment(self, key: str, value: dict[str, Any]) -> None:
        self.data.setdefault("enrichment", {})[key] = value
        self._dirty = True

    def get_music(self, key: str) -> dict[str, Any] | None:
        return self.data.get("music", {}).get(key)

    def set_music(self, key: str, value: dict[str, Any]) -> None:
        self.data.setdefault("music", {})[key] = value
        self._dirty = True

    def delete_music(self, key: str) -> None:
        self.data.setdefault("music", {}).pop(key, None)
        self._dirty = True

    @contextmanager
    def batch(self):
        self._batch_depth += 1
        try:
            yield self
        finally:
            self._batch_depth -= 1
            if self._batch_depth == 0:
                self.save()


class NullCache(Cache):
    def __init__(self) -> None:
        self.path = Path(".")
        self.data = {
            "schema_version": CACHE_SCHEMA_VERSION,
            "shows": {},
            "movies": {},
            "enrichment": {},
            "music": {},
        }
        self._dirty = False
        self._batch_depth = 0

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

    def save(self, force: bool = False) -> None:
        return None

    def save_with_status(self, force: bool = False) -> bool:
        return True

    def get_enrichment(self, key: str) -> dict[str, Any] | None:
        return None

    def set_enrichment(self, key: str, value: dict[str, Any]) -> None:
        return None

    def get_music(self, key: str) -> dict[str, Any] | None:
        return None

    def set_music(self, key: str, value: dict[str, Any]) -> None:
        return None

    def delete_music(self, key: str) -> None:
        return None

    @contextmanager
    def batch(self):
        yield self
