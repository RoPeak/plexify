from pathlib import Path

from plexify import cli
from plexify.util import build_cache_key


def test_cache_key_uses_relative_path_and_normalised_stem() -> None:
    incoming = Path("incoming")
    path = incoming / "Movies" / "Gladiator.2000.mkv"
    key = build_cache_key(path, incoming, "movie", 2000)
    assert key.startswith("movie|")
    assert "movies/gladiator.2000.mkv" in key
    assert "gladiator" in key
    assert "2000" in key


def test_cache_year_compatibility() -> None:
    assert cli._cache_entry_compatible(2000, 2001) is True
    assert cli._cache_entry_compatible(2000, 2010) is False
