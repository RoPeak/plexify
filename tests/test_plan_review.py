from pathlib import Path
from types import SimpleNamespace

import pytest
import typer

from plexify.services.organise_service import run_video_workflow
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

    with pytest.raises(typer.Exit):
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
            typer_module=typer,
        )

    review = "\n".join(console.lines)
    assert review.count("SOURCE:") == 7
    assert review.count("DESTINATION:") == 7
    assert "Movie 6 (2006)" in review
    assert "confidence 0.910" in review
    assert "1 fallback search" in review
    assert events == ["approval"]
    assert planning_roots == [tmp_path]
