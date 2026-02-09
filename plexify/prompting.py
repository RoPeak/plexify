from __future__ import annotations

from rich.progress import Progress
from rich.prompt import Prompt


def _pause_progress(progress: Progress | None) -> bool:
    if progress is not None and getattr(progress, "disable", False):
        return False
    if progress is not None and getattr(progress, "live", None):
        progress.stop()
        return True
    return False


def _resume_progress(progress: Progress | None, was_running: bool) -> None:
    if progress is not None and was_running:
        progress.start()


def _prompt_text(prompt: str, default: str, progress: Progress | None, show_default: bool = True) -> str:
    was_running = _pause_progress(progress)
    try:
        return Prompt.ask(prompt, default=default, show_default=show_default)
    finally:
        _resume_progress(progress, was_running)
