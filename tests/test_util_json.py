from pathlib import Path

from plexify.util import json_load


def test_json_load_recovers_from_corrupt_json(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{oops", encoding="utf-8")

    data = json_load(path)

    assert data == {}
    renamed = list(tmp_path.glob("bad.json.corrupt-*"))
    assert renamed
    assert not path.exists()

