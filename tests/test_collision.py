from pathlib import Path

from plexify.util import unique_path


def test_unique_path_appends_counter(tmp_path: Path):
    target = tmp_path / "file.mkv"
    target.write_text("one")
    candidate = unique_path(target)
    assert candidate.name == "file (2).mkv"
