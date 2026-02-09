from plexify import prompting


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
