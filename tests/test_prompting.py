from plexify import prompting, prompting_ui


def test_pause_progress_noop_when_disabled() -> None:
    class ProgressStub:
        disable = True
        live = True

        def __init__(self) -> None:
            self.stopped = False

        def stop(self) -> None:
            self.stopped = True

    progress = ProgressStub()
    was_running = prompting._pause_progress(progress)
    assert was_running is False
    assert progress.stopped is False


def test_prompt_line_includes_back_and_next_when_enabled() -> None:
    line = prompting_ui.prompt_line(
        has_candidates=True,
        allow_search=True,
        allow_manual=True,
        has_more=True,
        allow_back=True,
        prompt_base="base",
    )
    assert "b=back" in line
    assert "n=next page" in line
