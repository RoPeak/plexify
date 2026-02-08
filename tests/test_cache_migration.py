from pathlib import Path

from plexify.cache import CACHE_SCHEMA_VERSION, Cache
from plexify.util import json_dump


def test_legacy_cache_without_schema_version_upgrades(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache.json"
    json_dump(cache_path, {"shows": {"k": {"name": "Show"}}})

    cache = Cache(cache_path)

    assert cache.data.get("schema_version") == CACHE_SCHEMA_VERSION
    assert "shows" in cache.data
    assert "movies" in cache.data
    assert "enrichment" in cache.data


def test_cache_upgrade_adds_missing_sections(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache.json"
    json_dump(cache_path, {"schema_version": CACHE_SCHEMA_VERSION, "shows": {}})

    cache = Cache(cache_path)

    assert isinstance(cache.data["movies"], dict)
    assert isinstance(cache.data["enrichment"], dict)


def test_cache_upgrade_handles_non_dict_payload(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache.json"
    cache_path.write_text("[]", encoding="utf-8")

    cache = Cache(cache_path)

    assert cache.data.get("schema_version") == CACHE_SCHEMA_VERSION
    assert cache.data["shows"] == {}
    assert cache.data["movies"] == {}
    assert cache.data["enrichment"] == {}


def test_cache_save_persists_schema_version(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache.json"
    cache = Cache(cache_path)
    cache.set_show("tv|k", {"name": "Show"})
    cache.save()

    reloaded = Cache(cache_path)
    assert reloaded.data.get("schema_version") == CACHE_SCHEMA_VERSION
