from pathlib import Path
from types import SimpleNamespace

import pytest

from plexify.commands.video_flow import print_run_summary
from plexify.services.organise_service import _print_complete_plan, run_video_workflow
from plexify.util import MovePlan


class _Console:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, value, *_args, **_kwargs) -> None:
        self.lines.append(str(value))


def test_apply_requires_complete_plan_review_before_plan_approval(tmp_path: Path) -> None:
    incoming = tmp_path / "incoming"
    library = tmp_path / "Movies"
    plans = [
        MovePlan(
            source=incoming / f"source-{index}.mkv",
            destination=library / f"Movie {index}" / f"Movie {index}.mkv",
            mode="apply",
            media_type="movie",
            metadata={
                "title": f"Movie {index}",
                "year": 2000 + index,
                "selection": {"confidence": 0.91, "fallback_attempts": 1 if index == 2 else 0},
            },
        )
        for index in range(7)
    ]
    console = _Console()
    events: list[str] = []
    options = SimpleNamespace(
        incoming=incoming,
        library=library,
        mode="apply",
        copy_mode=True,
        extensions=".mkv",
        min_confidence=0.9,
        cache=None,
        report=None,
        yes=True,
        limit=None,
        print_tree=False,
        interactive_mode=True,
        media_type="movie",
        no_cache=True,
        clear_cache=False,
        offline=True,
        quiet=False,
        on_conflict="rename",
        prune_empty_dirs=False,
        prune_ignore="",
        allow_risky_enter_accept=False,
        strict_safe=False,
        plain_output=True,
        platform="auto",
        run_id="test",
        category_root=True,
        build_tree_fn=lambda _paths: None,
        skip_reason_lines_fn=lambda _stats: [],
        rich_escape_fn=str,
    )

    def approve(*_args, **_kwargs):
        events.append("approval")
        return False

    planning_roots: list[Path] = []

    def plan_items(**kwargs):
        planning_roots.append(kwargs["library"])
        return plans, [], SimpleNamespace(skipped=0, errors=0)

    run_video_workflow(
        options=options,
        console=console,
        plan_items_fn=plan_items,
        select_preview_plans_fn=lambda values: values,
        preview_spans_multiple_groups_fn=lambda _values: False,
        confirm_move_fn=lambda *_args: False,
        confirm_fn=approve,
        confirm_overwrite_apply_fn=lambda *_args: True,
        apply_with_streamed_report_fn=lambda *_args, **_kwargs: pytest.fail("cancelled plan must not apply"),
        execute_plans_fn=lambda *_args, **_kwargs: pytest.fail("cancelled plan must not apply"),
        prune_empty_dirs_fn=lambda *_args, **_kwargs: None,
        parse_prune_ignore_fn=lambda _value: set(),
        write_report_fn=lambda *_args, **_kwargs: None,
        print_run_summary_fn=lambda **_kwargs: None,
        build_command_config_cls=SimpleNamespace,
        build_command_fn=str,
        parse_extensions_fn=lambda value: [value],
        format_path_fn=str,
        now_timestamp_fn=lambda: "run",
        log_event_fn=lambda *_args, **_kwargs: None,
        logger=None,
        typer_module=SimpleNamespace(Exit=Exception),
        )

    review = "\n".join(console.lines)
    assert "Cancelled. No filesystem changes were made." in review
    assert review.count("SOURCE:") == 7
    assert review.count("DESTINATION:") == 7
    assert "Movie 6 (2006)" in review
    assert "confidence 0.910" in review
    assert "1 fallback search" in review
    assert events == ["approval"]
    assert planning_roots == [tmp_path]


def test_tv_complete_plan_groups_episodes_with_exact_mappings(tmp_path: Path) -> None:
    incoming = tmp_path / "incoming"
    library = tmp_path / "TV"
    plans = [
        MovePlan(incoming / f"episode-{episode}.mkv", library / "Show" / f"S01E{episode:02d}.mkv", "dry-run", "tv", {
            "show": "Example Show", "year": 2020, "season": 1, "episode": episode,
        })
        for episode in (2, 1)
    ]
    console = _Console()
    _print_complete_plan(console, plans, on_conflict="rename", copy_mode=True, format_path_fn=str, heading="Review")
    output = "\n".join(console.lines)
    assert "Example Show (2020) — 2 episode(s)" in output
    assert output.index("S01E01") < output.index("S01E02")
    assert "episode-1.mkv →" in output
    assert output.count("episode-") == 2


@pytest.mark.parametrize(("mode", "moved", "skipped", "expected"), [
    ("dry-run", [], ["planned"], ["Would publish: 1", "Published: 0", "Not executed because dry-run: 1", "Existing-destination conflicts skipped: 0"]),
    ("apply", ["published"], [], ["Published: 1", "Verified: 1", "Completed successfully."]),
])
def test_run_summary_distinguishes_dry_run_from_apply(mode, moved, skipped, expected) -> None:
    console = _Console()
    stats = SimpleNamespace(
        discovered=1, skipped=0, manual_skip=0, no_candidates=0, offline_no_cache=0, filtered_media_type=0,
        auto_matched=1, user_confirmed=0, manual=0, cache_hits=0, conflict_skip=0,
        renamed_conflicts=0, elapsed=0.0,
    )
    print_run_summary(
        console=console, format_path_fn=str, stats=stats, plans=[object()], errors=[],
        result=SimpleNamespace(moved=moved, skipped=skipped, errors=[]),
        cache_path=None, report_path=None, mode=mode,
    )
    output = "\n".join(console.lines)
    for value in expected:
        assert value in output
