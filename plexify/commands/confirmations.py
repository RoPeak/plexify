from __future__ import annotations

from typing import Callable

from ..util import MovePlan


def confirm_move(prompt_text: Callable[[str, str, object | None], str], progress: object | None) -> bool:
    phrase = prompt_text("To proceed, type MOVE", "", progress)
    return phrase.strip().lower() == "move"


def confirm_overwrite_apply(
    plans: list[MovePlan],
    copy_mode: bool,
    prompt_text: Callable[[str, str, object | None], str],
    print_line: Callable[[str], None],
) -> bool:
    operation = "copy" if copy_mode else "move"
    print_line("Warning: overwrite mode will replace existing destination files.")
    print_line(f"Apply mode: {operation} | Planned items: {len(plans)} | Conflict policy: overwrite")
    phrase = prompt_text("To proceed, type OVERWRITE", "", None)
    return phrase.strip() == "OVERWRITE"

