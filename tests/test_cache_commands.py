from pathlib import Path

from typer.testing import CliRunner

from plexify.cache import Cache
from plexify.cli import app


def test_cache_stats_reports_entry_counts(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache.json"
    cache = Cache(cache_path)
    cache.set_show("tv|show|2000", {"id": 1, "name": "Show", "confirmed_by_user": True})
    cache.set_movie("movie|film|2001", {"qid": "Q1", "title": "Film", "confirmed_by_user": True})
    cache.set_enrichment("wikidata:Q1", {"director": "A"})
    cache.set_music("music|artist|album|unknown|1|abc", {"decision": "filename_fallback"})
    cache.save()

    runner = CliRunner()
    result = runner.invoke(app, ["cache", "stats", "--cache", str(cache_path)])

    assert result.exit_code == 0
    assert "Shows: 1" in result.output
    assert "Movies: 1" in result.output
    assert "Enrichment: 1" in result.output


def test_cache_prune_removes_unconfirmed_and_ambiguous_entries(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache.json"
    cache = Cache(cache_path)
    cache.set_show("tv|keep|2000", {"id": 1, "name": "Keep", "confirmed_by_user": True})
    cache.set_show("tv|drop|2001", {"id": 2, "name": "Drop", "confirmed_by_user": False})
    cache.set_movie("movie|ambiguous|unknown", {"ambiguous": True, "matches": []})
    cache.save()

    runner = CliRunner()
    result = runner.invoke(app, ["cache", "prune", "--cache", str(cache_path)])

    assert result.exit_code == 0
    updated = Cache(cache_path)
    assert updated.get_show("tv|keep|2000") is not None
    assert updated.get_show("tv|drop|2001") is None
    assert updated.get_movie("movie|ambiguous|unknown") is None


def test_cache_delete_removes_matching_keys_by_query(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache.json"
    cache = Cache(cache_path)
    cache.set_show("tv|doctor who|2005", {"id": 1, "name": "Doctor Who", "confirmed_by_user": True})
    cache.set_movie("movie|doctor strange|2016", {"qid": "Q1", "title": "Doctor Strange", "confirmed_by_user": True})
    cache.set_movie("movie|inception|2010", {"qid": "Q2", "title": "Inception", "confirmed_by_user": True})
    cache.save()

    runner = CliRunner()
    result = runner.invoke(app, ["cache", "delete", "doctor", "--cache", str(cache_path)])

    assert result.exit_code == 0
    updated = Cache(cache_path)
    assert updated.get_show("tv|doctor who|2005") is None
    assert updated.get_movie("movie|doctor strange|2016") is None
    assert updated.get_movie("movie|inception|2010") is not None
