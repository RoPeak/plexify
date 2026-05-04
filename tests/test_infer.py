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


def test_infer_tv_leading_zero_episode_special_in_season_zero_folder(tmp_path: Path) -> None:
    season_dir = tmp_path / "Show" / "Season 0"
    season_dir.mkdir(parents=True)
    first = season_dir / "0 - Pilot.mkv"
    second = season_dir / "1 - Episode One.mkv"
    first.write_text("x", encoding="utf-8")
    second.write_text("x", encoding="utf-8")

    item = infer_item(first)
    assert item.media_type == "tv"
    assert item.title == "Show"
    assert item.season == 0
    assert item.episode == 0
    assert item.episode_title == "Pilot"


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


def test_infer_blade_runner_2049_is_not_tv() -> None:
    item = infer_item(Path("Blade_Runner_2049_-__m0012ygz_original.mp4"))
    assert item.media_type == "movie"
    assert item.year == 2049
    assert item.season is None
    assert item.episode is None


def test_infer_district_9_is_not_tv() -> None:
    item = infer_item(Path("District_9.mkv"))
    assert item.media_type == "movie"
    assert item.year is None
    assert item.season is None
    assert item.episode is None


def test_infer_studio_54_is_not_tv() -> None:
    item = infer_item(Path("Studio_54.mkv"))
    assert item.media_type == "movie"
    assert item.year is None
    assert item.season is None
    assert item.episode is None


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


def test_infer_divergent_series_movie_stays_movie_under_movies_folder() -> None:
    path = Path("C:/Video/Unorganised/Movies/The Divergent Series - Allegiant (2016)/The Divergent Series - Allegiant (2016).mp4")
    item = infer_item(path)
    assert item.media_type == "movie"
    assert item.season is None
    assert item.episode is None


def test_infer_movie_generic_stem_uses_parent_folder_title() -> None:
    path = Path("About Time (2013)/C1_t00.mkv")
    item = infer_item(path)
    assert item.media_type == "movie"
    assert item.title == "About Time"
    assert item.year == 2013


def test_infer_numeric_movie_title_folder_does_not_become_year() -> None:
    item = infer_item(Path("1917/B1_t00.mkv"))
    assert item.media_type == "movie"
    assert item.title == "1917"
    assert item.year is None


def test_infer_parent_title_prefixed_disc_suffix_uses_parent_title() -> None:
    item = infer_item(Path("Cloudy With A Chance Of Meatballs 2/Cloudy With A Chance Of Meatballs 2-B1_t00.mkv"))
    assert item.media_type == "movie"
    assert item.title == "Cloudy With A Chance Of Meatballs 2"
    assert item.year is None


def test_infer_movie_generic_stem_with_multi_letter_prefix_uses_parent_folder_title() -> None:
    path = Path("Wallace and Gromit The Curse of the Were-Rabbit (2005)/PC1_t05.mkv")
    item = infer_item(path)
    assert item.media_type == "movie"
    assert item.title == "Wallace and Gromit The Curse of the Were-Rabbit"
    assert item.year == 2005


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


def test_infer_tv_series_folder_does_not_become_title(tmp_path: Path) -> None:
    season_dir = tmp_path / "Gotham" / "Series 1"
    season_dir.mkdir(parents=True)
    episode_one = season_dir / "1. Pilot.mkv"
    episode_two = season_dir / "2. Selina Kyle.mkv"
    episode_one.write_text("x", encoding="utf-8")
    episode_two.write_text("x", encoding="utf-8")

    item = infer_item(episode_one)
    assert item.media_type == "tv"
    assert item.title == "Gotham"
    assert item.season == 1
    assert item.episode == 1


def test_infer_tv_season_folder_does_not_become_title(tmp_path: Path) -> None:
    season_dir = tmp_path / "Gotham" / "Season 1"
    season_dir.mkdir(parents=True)
    episode_one = season_dir / "1. Pilot.mkv"
    episode_two = season_dir / "2. Selina Kyle.mkv"
    episode_one.write_text("x", encoding="utf-8")
    episode_two.write_text("x", encoding="utf-8")

    item = infer_item(episode_one)
    assert item.media_type == "tv"
    assert item.title == "Gotham"
    assert item.season == 1
    assert item.episode == 1


def test_infer_tv_episode_range_from_leading_numbers(tmp_path: Path) -> None:
    season_dir = tmp_path / "Smallville" / "Season 10"
    season_dir.mkdir(parents=True)
    first = season_dir / "20. Prophecy.m4v"
    finale = season_dir / "21-22. Finale.m4v"
    first.write_text("x", encoding="utf-8")
    finale.write_text("x", encoding="utf-8")

    item = infer_item(finale)
    assert item.media_type == "tv"
    assert item.title == "Smallville"
    assert item.season == 10
    assert item.episode == 21
    assert item.episode_end == 22
    assert item.episode_title == "Finale"


@pytest.mark.parametrize(
    ("filename", "expected_start", "expected_end"),
    [
        ("4 and 5.mkv", 4, 5),
        ("11 & 12.mkv", 11, 12),
        ("17 to 18.mkv", 17, 18),
    ],
)
def test_infer_tv_episode_range_from_word_or_symbol_separators(
    tmp_path: Path, filename: str, expected_start: int, expected_end: int
) -> None:
    season_dir = tmp_path / "The Office" / "Season 6"
    season_dir.mkdir(parents=True)
    (season_dir / "1.mkv").write_text("x", encoding="utf-8")
    episode_file = season_dir / filename
    episode_file.write_text("x", encoding="utf-8")

    item = infer_item(episode_file)
    assert item.media_type == "tv"
    assert item.title == "The Office"
    assert item.season == 6
    assert item.episode == expected_start
    assert item.episode_end == expected_end
    assert item.episode_title is None


def test_infer_tv_episode_range_from_chained_sxxeyy_token() -> None:
    path = Path("Show/Season 1/Show.S01E01E02.mkv")
    item = infer_item(path)
    assert item.media_type == "tv"
    assert item.title == "Show"
    assert item.season == 1
    assert item.episode == 1
    assert item.episode_end == 2


def test_infer_tv_episode_range_from_chained_xyy_token() -> None:
    path = Path("Show/Season 1/Show.1x01x02.mkv")
    item = infer_item(path)
    assert item.media_type == "tv"
    assert item.title == "Show"
    assert item.season == 1
    assert item.episode == 1
    assert item.episode_end == 2


def test_infer_tv_specials_multi_episode_from_chained_sxxeyy_token() -> None:
    path = Path("Show/Specials/Show.S00E01E02.mkv")
    item = infer_item(path)
    assert item.media_type == "tv"
    assert item.season == 0
    assert item.episode == 1
    assert item.episode_end == 2


def test_infer_tv_root_level_season_folder_uses_folder_name_as_show_title(tmp_path: Path) -> None:
    season_dir = tmp_path / "DC's Legends of Tomorrow Season 1"
    season_dir.mkdir(parents=True)
    episode_one = season_dir / "1. Pilot, Part 1.mkv"
    episode_two = season_dir / "2. Pilot, Part 2.mkv"
    episode_one.write_text("x", encoding="utf-8")
    episode_two.write_text("x", encoding="utf-8")

    item = infer_item(episode_one)
    assert item.media_type == "tv"
    assert item.title == "DC's Legends of Tomorrow"
    assert item.season == 1
    assert item.episode == 1
