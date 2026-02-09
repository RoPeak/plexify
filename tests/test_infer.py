from pathlib import Path

import pytest

from plexify.infer import infer_item


def test_infer_tv_from_season_folder():
    path = Path("Breaking Bad/Season 1/2.mkv")
    item = infer_item(path)
    assert item.media_type == "tv"
    assert item.title == "Breaking Bad"
    assert item.season == 1
    assert item.episode == 2


def test_infer_tv_leading_episode_number_with_separator() -> None:
    path = Path("Sherlock/Season 1/2 - The Blind Banker.mkv")
    item = infer_item(path)
    assert item.media_type == "tv"
    assert item.season == 1
    assert item.episode == 2


def test_infer_tv_leading_zero_episode_number_with_separator() -> None:
    path = Path("Sherlock/Season 1/02 - The Blind Banker.mkv")
    item = infer_item(path)
    assert item.media_type == "tv"
    assert item.season == 1
    assert item.episode == 2


def test_infer_tv_sxxeyy_still_wins() -> None:
    path = Path("Sherlock/Season 1/S01E02 - The Blind Banker.mkv")
    item = infer_item(path)
    assert item.media_type == "tv"
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


def test_infer_movie_title_keeps_hyphen_subtitle() -> None:
    path = Path("Bridget Jones - The Edge of Reason (2004).mkv")
    item = infer_item(path)
    assert item.media_type == "movie"
    assert item.title.startswith("Bridget Jones")
    assert "Edge of Reason" in item.title
    assert item.year == 2004


def test_infer_movie_title_without_hyphen_unchanged() -> None:
    path = Path("Inception (2010).mkv")
    item = infer_item(path)
    assert item.media_type == "movie"
    assert item.title == "Inception"


def test_infer_movie_hyphen_noise_ignored() -> None:
    path = Path("Movie - 1080p.mkv")
    item = infer_item(path)
    assert item.media_type == "movie"
    assert item.title == "Movie"


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


def test_infer_numbered_episode_in_season_folder(tmp_path: Path) -> None:
    season_dir = tmp_path / "Show" / "Season 2"
    season_dir.mkdir(parents=True)
    first = season_dir / "1.mkv"
    second = season_dir / "2.mkv"
    first.write_text("one", encoding="utf-8")
    second.write_text("two", encoding="utf-8")

    item = infer_item(first)
    assert item.media_type == "tv"
    assert item.title == "Show"
    assert item.season == 2
    assert item.episode == 1

    item = infer_item(second)
    assert item.media_type == "tv"
    assert item.title == "Show"
    assert item.season == 2
    assert item.episode == 2


def test_infer_tv_parent_name_fallback(tmp_path: Path) -> None:
    folder = tmp_path / "Some Show"
    folder.mkdir(parents=True)
    video = folder / "S01E01.mkv"
    video.write_text("one", encoding="utf-8")
    item = infer_item(video)
    assert item.media_type == "tv"
    assert item.title == "Some Show"


def test_infer_tv_under_movies_folder_with_series_pattern() -> None:
    path = Path(
        "C:/Video/Unorganised/Movies/The_Young_Offenders_Series_2_-_01._Episode_1_p07rqh8m_editorial.mp4"
    )
    item = infer_item(path)
    assert item.media_type == "tv"


def test_infer_tv_series_episode_night_manager() -> None:
    path = Path("The_Night_Manager_Series_2_-_01._Episode_1_p01.mp4")
    item = infer_item(path)
    assert item.media_type == "tv"


def test_infer_tv_series_episode_young_offenders() -> None:
    path = Path("The_Young_Offenders_Series_1_-_04._Episode_4_p02.mp4")
    item = infer_item(path)
    assert item.media_type == "tv"
    assert item.season == 1
    assert item.episode == 4
    assert "Movies" not in item.title


def test_infer_tv_prefers_deepest_season_folder() -> None:
    path = Path("Season 01/ShowName/Season 02/03 - Title.mkv")
    item = infer_item(path)
    assert item.media_type == "tv"
    assert item.title == "ShowName"
    assert item.season == 2
    assert item.episode == 3


def test_infer_tv_typoed_season_folder_token() -> None:
    path = Path("The Big Bang Theory/The Big Bang Theory Seaon 5/1. The Skank Reflex Analysis.m4v")
    item = infer_item(path)
    assert item.media_type == "tv"
    assert item.title == "The Big Bang Theory"
    assert item.season == 5
    assert item.episode == 1


@pytest.mark.parametrize(
    ("path", "expected_media_type", "expected_title", "expected_season", "expected_episode"),
    [
        ("Movies/The Last of Us/Season 01/S01E02.mkv", "tv", "The Last of Us", 1, 2),
        ("Show Name/Specials/Show Name - S00E01 - Pilot.mkv", "tv", "Specials", 0, 1),
        ("Season 01/ShowName/Season 02/03 - Title.mkv", "tv", "ShowName", 2, 3),
    ],
)
def test_infer_edge_cases_matrix(
    path: str,
    expected_media_type: str,
    expected_title: str,
    expected_season: int | None,
    expected_episode: int | None,
) -> None:
    item = infer_item(Path(path))
    assert item.media_type == expected_media_type
    assert item.title == expected_title
    assert item.season == expected_season
    assert item.episode == expected_episode


def test_infer_anime_style_numbering_in_season_folder(tmp_path: Path) -> None:
    season_dir = tmp_path / "Anime Show" / "Season 1"
    season_dir.mkdir(parents=True)
    episode_12 = season_dir / "12.mkv"
    episode_13 = season_dir / "13.mkv"
    episode_12.write_text("x", encoding="utf-8")
    episode_13.write_text("x", encoding="utf-8")

    item = infer_item(episode_12)
    assert item.media_type == "tv"
    assert item.title == "Anime Show"
    assert item.season == 1
    assert item.episode == 12


def test_infer_tv_leading_episode_without_season_folder(tmp_path: Path) -> None:
    show_dir = tmp_path / "Arrested Development"
    show_dir.mkdir(parents=True)
    episode_one = show_dir / "1. Pilot.mkv"
    episode_two = show_dir / "2. Top Banana.mkv"
    episode_one.write_text("x", encoding="utf-8")
    episode_two.write_text("x", encoding="utf-8")

    item = infer_item(episode_one)
    assert item.media_type == "tv"
    assert item.title == "Arrested Development"
    assert item.season == 1
    assert item.episode == 1
    assert item.episode_title == "Pilot"
