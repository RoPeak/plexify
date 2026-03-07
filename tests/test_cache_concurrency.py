from pathlib import Path

from plexify.cache import Cache
from plexify.util import json_load


def test_cache_merge_preserves_disjoint_writes(tmp_path: Path) -> None:
    path = tmp_path / "cache.json"
    first = Cache(path)
    second = Cache(path)

    first.set_movie("movie|alpha|2001", {"qid": "Q1", "title": "Alpha", "year": 2001})
    second.set_movie("movie|beta|2002", {"qid": "Q2", "title": "Beta", "year": 2002})

    assert first.save_with_status() is True
    assert second.save_with_status() is True

    data = json_load(path)
    movies = data.get("movies", {})
    assert "movie|alpha|2001" in movies
    assert "movie|beta|2002" in movies


def test_cache_merge_preserves_delete_and_disjoint_set(tmp_path: Path) -> None:
    path = tmp_path / "cache.json"
    seed = Cache(path)
    seed.set_movie("movie|old|2000", {"qid": "Q-old", "title": "Old", "year": 2000})
    seed.set_movie("movie|keep|2005", {"qid": "Q-keep", "title": "Keep", "year": 2005})
    assert seed.save_with_status() is True

    deleter = Cache(path)
    writer = Cache(path)

    deleter.delete_movie("movie|old|2000")
    writer.set_movie("movie|new|2024", {"qid": "Q-new", "title": "New", "year": 2024})

    # Save writer first to simulate stale in-memory state in deleter.
    assert writer.save_with_status() is True
    assert deleter.save_with_status() is True

    data = json_load(path)
    movies = data.get("movies", {})
    assert "movie|old|2000" not in movies
    assert "movie|new|2024" in movies
    assert "movie|keep|2005" in movies


def test_cache_batch_persists_multiple_section_mutations(tmp_path: Path) -> None:
    path = tmp_path / "cache.json"
    cache = Cache(path)

    with cache.batch():
        cache.set_show("tv|show|2001", {"id": 1, "name": "Show", "premiered": 2001})
        cache.set_movie("movie|film|2001", {"qid": "Q-film", "title": "Film", "year": 2001})
        cache.set_enrichment("wikidata:Q-film", {"director": "Name"})
        cache.set_music("music|artist|album", {"artist": "Artist", "album": "Album"})

    data = json_load(path)
    assert data.get("shows", {}).get("tv|show|2001") is not None
    assert data.get("movies", {}).get("movie|film|2001") is not None
    assert data.get("enrichment", {}).get("wikidata:Q-film") is not None
    assert data.get("music", {}).get("music|artist|album") is not None
