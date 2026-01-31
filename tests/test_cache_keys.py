from pathlib import Path

from plexify import cli
from plexify.util import build_cache_key, movie_cache_key, tv_episode_cache_key, tv_show_cache_key


def test_cache_key_uses_relative_path_and_normalised_stem() -> None:
    incoming = Path("incoming")
    path = incoming / "Movies" / "Gladiator.2000.mkv"
    key = build_cache_key(path, incoming, "movie", 2000)
    assert key.startswith("movie|")
    assert "movies/gladiator.2000.mkv" in key
    assert "gladiator" in key
    assert "2000" in key


def test_reusable_cache_keys() -> None:
    movie_key = movie_cache_key("Superman II", 1980)
    show_key = tv_show_cache_key("Doctor Who", 2005)
    episode_key = tv_episode_cache_key("Doctor Who", 2005, 1, 1)
    assert movie_key == "movie|superman 2|1980"
    assert show_key == "tv|doctor who|2005"
    assert episode_key == "tv|doctor who|2005|s1|e1"


def test_cache_year_compatibility() -> None:
    assert cli._cache_entry_compatible(2000, 2001) is True
    assert cli._cache_entry_compatible(2000, 2010) is False
