from pathlib import Path

from plexify.util import iter_video_files


def test_iter_video_files_uses_natural_numeric_order(tmp_path: Path) -> None:
    incoming = tmp_path / "incoming" / "Show" / "Season 1"
    incoming.mkdir(parents=True)
    for name in ["Episode 1.mkv", "Episode 10.mkv", "Episode 11.mkv", "Episode 2.mkv", "Episode 3.mkv"]:
        (incoming / name).write_text("x", encoding="utf-8")

    files = iter_video_files(tmp_path / "incoming", [".mkv"])

    assert [path.name for path in files] == [
        "Episode 1.mkv",
        "Episode 2.mkv",
        "Episode 3.mkv",
        "Episode 10.mkv",
        "Episode 11.mkv",
    ]
