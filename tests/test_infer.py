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


def test_infer_movie_leading_number_preserved() -> None:
    path = Path("28.Days.Later.2002.1080p.BluRay.x264.mkv")
    item = infer_item(path)
    assert item.media_type == "movie"
    assert item.title == "28 Days Later"


def test_infer_tv_series_separator_pattern() -> None:
    path = Path("Show/Series_6_-_01.mkv")
    item = infer_item(path)
    assert item.media_type == "tv"
    assert item.season == 6
    assert item.episode == 1


def test_infer_tv_series_separator_underscores() -> None:
    path = Path("Show/Series_6_-_04_extra.mkv")
    item = infer_item(path)
    assert item.media_type == "tv"
    assert item.season == 6
    assert item.episode == 4


def test_infer_tv_year_range_and_episode_title() -> None:
    path = Path("Doctor Who/Series_1/Doctor_Who_2005-2022_-_01_Rose.mkv")
    item = infer_item(path)
    assert item.media_type == "tv"
    assert item.title == "Doctor Who"
    assert item.year == 2005
    assert item.season == 1
    assert item.episode == 1
    assert item.episode_title == "Rose"


def test_infer_numbered_episode_folder(tmp_path: Path) -> None:
    show_dir = tmp_path / "Pride and Prejudice (1995 BBC Show)"
    show_dir.mkdir(parents=True)
    first = show_dir / "1.mkv"
    second = show_dir / "2.mkv"
    first.write_text("one", encoding="utf-8")
    second.write_text("two", encoding="utf-8")

    item = infer_item(first)
    assert item.media_type == "tv"
    assert item.title == "Pride and Prejudice"
    assert item.year == 1995
    assert item.season == 1
    assert item.episode == 1


def test_infer_tv_parent_name_fallback(tmp_path: Path) -> None:
    folder = tmp_path / "Some Show"
    folder.mkdir(parents=True)
    video = folder / "S01E01.mkv"
    video.write_text("one", encoding="utf-8")
    item = infer_item(video)
    assert item.media_type == "tv"
    assert item.title == "Some Show"
