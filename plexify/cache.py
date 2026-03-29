from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .logging_config import get_logger
from .models import CacheData
from .util import json_dump, json_load

CACHE_SCHEMA_VERSION = 3
MIN_SUPPORTED_SCHEMA_VERSION = 1
LOCK_TIMEOUT_SECONDS = 1.0
LOCK_RETRY_DELAY_SECONDS = 0.05
STALE_LOCK_SECONDS = 300.0
logger = get_logger(__name__)


@dataclass
class CacheEntry:
    key: str
    value: dict[str, Any]


def _upgrade_cache_data(data: Any) -> CacheData:
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
    upgraded.setdefault("entities", {})
    upgraded.setdefault("music", {})
    upgraded["schema_version"] = CACHE_SCHEMA_VERSION
    return upgraded


class Cache:
    _MUTABLE_SECTIONS = ("shows", "movies", "enrichment", "entities", "music")

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data = _upgrade_cache_data(json_load(path))
        self._dirty = False
        self._batch_depth = 0
        self._pending_set: dict[str, dict[str, dict[str, Any]]] = {
            section: {} for section in self._MUTABLE_SECTIONS
        }
        self._pending_delete: dict[str, set[str]] = {
            section: set() for section in self._MUTABLE_SECTIONS
        }

    def get_show(self, key: str) -> dict[str, Any] | None:
        return self.data.get("shows", {}).get(key)

    def set_show(self, key: str, value: dict[str, Any]) -> None:
        self._mark_set("shows", key, value)

    def get_movie(self, key: str) -> dict[str, Any] | None:
        return self.data.get("movies", {}).get(key)

    def set_movie(self, key: str, value: dict[str, Any]) -> None:
        self._mark_set("movies", key, value)

    def delete_show(self, key: str) -> None:
        self._mark_delete("shows", key)

    def delete_movie(self, key: str) -> None:
        self._mark_delete("movies", key)

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
                if self._try_reclaim_stale_lock(lock_path):
                    continue
                if time.monotonic() >= deadline:
                    logger.warning("cache_lock_timeout", extra={"path": str(lock_path)})
                    return None
                time.sleep(LOCK_RETRY_DELAY_SECONDS)
            except OSError as exc:
                logger.warning("cache_lock_error", extra={"path": str(lock_path), "error": str(exc)})
                return None

    def _try_reclaim_stale_lock(self, lock_path: Path) -> bool:
        try:
            contents = lock_path.read_text(encoding="utf-8").strip()
        except OSError:
            return False
        stale = False
        try:
            created_at = float(contents)
        except ValueError:
            stale = True
        else:
            stale = (time.time() - created_at) >= STALE_LOCK_SECONDS
        if not stale:
            return False
        try:
            lock_path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("cache_stale_lock_cleanup_failed", extra={"path": str(lock_path), "error": str(exc)})
            return False
        logger.warning("cache_stale_lock_reclaimed", extra={"path": str(lock_path)})
        return True

    def save(self, force: bool = False) -> None:
        self.save_with_status(force=force)

    def _mark_set(self, section: str, key: str, value: dict[str, Any]) -> None:
        self.data.setdefault(section, {})[key] = value
        self._pending_set[section][key] = value
        self._pending_delete[section].discard(key)
        self._dirty = True

    def _mark_delete(self, section: str, key: str) -> None:
        self.data.setdefault(section, {}).pop(key, None)
        self._pending_delete[section].add(key)
        self._pending_set[section].pop(key, None)
        self._dirty = True

    def _has_pending_ops(self) -> bool:
        return any(self._pending_set[section] for section in self._MUTABLE_SECTIONS) or any(
            self._pending_delete[section] for section in self._MUTABLE_SECTIONS
        )

    def _clear_pending_ops(self) -> None:
        for section in self._MUTABLE_SECTIONS:
            self._pending_set[section].clear()
            self._pending_delete[section].clear()

    def _merge_pending_ops_into(self, base_data: dict[str, Any]) -> tuple[dict[str, int], dict[str, int]]:
        set_counts: dict[str, int] = {}
        delete_counts: dict[str, int] = {}
        for section in self._MUTABLE_SECTIONS:
            target = base_data.setdefault(section, {})
            pending_set = self._pending_set[section]
            pending_delete = self._pending_delete[section]
            for key in pending_delete:
                target.pop(key, None)
            for key, value in pending_set.items():
                target[key] = value
            set_counts[section] = len(pending_set)
            delete_counts[section] = len(pending_delete)
        base_data["schema_version"] = CACHE_SCHEMA_VERSION
        return set_counts, delete_counts

    def save_with_status(self, force: bool = False) -> bool:
        if self._batch_depth > 0 and not force:
            return True
        if not self._dirty and not force and not self._has_pending_ops():
            return True
        lock_path = self._acquire_lock()
        if lock_path is None:
            logger.warning("cache_save_skipped_lock_timeout", extra={"path": str(self.path)})
            return False
        try:
            if force and not self._has_pending_ops():
                merged = _upgrade_cache_data(self.data)
                json_dump(self.path, merged)
                self.data = merged
                self._dirty = False
                return True

            merged = _upgrade_cache_data(json_load(self.path))
            set_counts, delete_counts = self._merge_pending_ops_into(merged)
            json_dump(self.path, merged)
            logger.info(
                "cache_save_merge_applied",
                extra={
                    "path": str(self.path),
                    "set_counts": set_counts,
                    "delete_counts": delete_counts,
                },
            )
            self.data = merged
            self._clear_pending_ops()
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
        self._mark_set("enrichment", key, value)

    def get_entity(self, key: str) -> dict[str, Any] | None:
        return self.data.get("entities", {}).get(key)

    def set_entity(self, key: str, value: dict[str, Any]) -> None:
        self._mark_set("entities", key, value)

    def get_music(self, key: str) -> dict[str, Any] | None:
        return self.data.get("music", {}).get(key)

    def set_music(self, key: str, value: dict[str, Any]) -> None:
        self._mark_set("music", key, value)

    def delete_music(self, key: str) -> None:
        self._mark_delete("music", key)

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
            "entities": {},
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

    def get_entity(self, key: str) -> dict[str, Any] | None:
        return None

    def set_entity(self, key: str, value: dict[str, Any]) -> None:
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
