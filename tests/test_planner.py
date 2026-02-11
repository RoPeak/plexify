from pathlib import Path

from plexify.planner import plan_tv_show


def test_plan_tv_show_episode_range_filename() -> None:
    destination = plan_tv_show(
        library=Path("D:/Media"),
        show_name="Smallville",
        year=2001,
        season=10,
        episode=21,
        episode_end=22,
        episode_title="Finale",
        ext=".m4v",
    )

    assert "s10e21-e22" in destination.name


def test_plan_tv_show_multi_episode_policy_keeps_single_file_with_range_token() -> None:
    destination = plan_tv_show(
        library=Path("D:/Media"),
        show_name="Show",
        year=2000,
        season=1,
        episode=1,
        episode_end=2,
        episode_title="Pilot",
        ext=".mkv",
    )

    assert destination.name.endswith(".mkv")
    assert "s01e01-e02" in destination.name
