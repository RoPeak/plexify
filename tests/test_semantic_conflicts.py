from pathlib import Path
from types import SimpleNamespace

from plexify import cli
from plexify.services.video_item_service import _resolve_semantic_conflict


class _Helpers:
    def __init__(self, answers: list[str] | None = None) -> None:
        self.answers = iter(answers or [])
        self.messages: list[str] = []

    def _safe_print(self, message, _progress=None) -> None:
        self.messages.append(str(message))

    def _prompt_text(self, _prompt, _default, _progress=None):
        return next(self.answers)

    @staticmethod
    def _record_stat(stats, outcome, *, reason=None) -> None:
        cli._record_stat(stats, outcome, reason=reason)


def _item(path: Path) -> SimpleNamespace:
    return SimpleNamespace(path=path)


def test_identical_episode_is_automatically_skipped_without_suffix(tmp_path: Path) -> None:
    incoming = tmp_path / "Incoming" / "Banshee S02E01 - Little Fish.mkv"
    destination = tmp_path / "TV Shows" / "Banshee (2013)" / "Season 02" / "Banshee (2013) - s02e01 - Little Fish.mkv"
    incoming.parent.mkdir(parents=True); destination.parent.mkdir(parents=True)
    incoming.write_bytes(b"same episode"); destination.write_bytes(b"same episode")
    stats = cli.PlanStats(); helpers = _Helpers()

    result = _resolve_semantic_conflict(
        item=_item(incoming), destination=destination, on_conflict="rename", interactive=True,
        progress=None, stats=stats, media_label="episode", helpers=helpers,
    )

    assert result.destination is None and result.identical_existing == destination
    assert stats.semantic_identical_skip == 1 and stats.skipped == 1
    assert not (destination.parent / f"{destination.stem} (2){destination.suffix}").exists()
    assert any("SHA-256 verified" in line for line in helpers.messages)


def test_differing_episode_requires_explicit_alternate_or_replace(tmp_path: Path) -> None:
    incoming = tmp_path / "Incoming" / "O'Brien Show S01E01.mkv"
    destination = tmp_path / "TV" / "O'Brien Show (2024) - s01e01 - Pilot.mkv"
    incoming.parent.mkdir(); destination.parent.mkdir(parents=True)
    incoming.write_bytes(b"incoming variant"); destination.write_bytes(b"existing variant")

    alternate = _resolve_semantic_conflict(
        item=_item(incoming), destination=destination, on_conflict="rename", interactive=True,
        progress=None, stats=cli.PlanStats(), media_label="episode", helpers=_Helpers(["a", "Director's Cut"]),
    )
    assert alternate.destination == destination.with_name("O'Brien Show (2024) - s01e01 - Pilot - Director's Cut.mkv")
    assert alternate.on_conflict == "rename"

    replacement = _resolve_semantic_conflict(
        item=_item(incoming), destination=destination, on_conflict="rename", interactive=True,
        progress=None, stats=cli.PlanStats(), media_label="episode", helpers=_Helpers(["r", "REPLACE"]),
    )
    assert replacement.destination == destination and replacement.on_conflict == "overwrite"


def test_differing_semantic_conflict_is_safe_skip_when_noninteractive(tmp_path: Path) -> None:
    incoming = tmp_path / "incoming.mkv"; destination = tmp_path / "library.mkv"
    incoming.write_bytes(b"incoming"); destination.write_bytes(b"existing")
    stats = cli.PlanStats()
    result = _resolve_semantic_conflict(
        item=_item(incoming), destination=destination, on_conflict="rename", interactive=False,
        progress=None, stats=stats, media_label="movie", helpers=_Helpers(),
    )
    assert result.destination is None
    assert stats.semantic_noninteractive_skip == 1 and stats.skipped == 1
