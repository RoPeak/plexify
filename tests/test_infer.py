from pathlib import Path

from plexify.infer import infer_item


def test_infer_tv_from_season_folder():
    path = Path("Breaking Bad/Season 1/2.mkv")
    item = infer_item(path)
    assert item.media_type == "tv"
    assert item.title == "Breaking Bad"
    assert item.season == 1
    assert item.episode == 2


def test_infer_movie_year():
    path = Path("The Matrix (1999).mkv")
    item = infer_item(path)
    assert item.media_type == "movie"
    assert item.year == 1999
