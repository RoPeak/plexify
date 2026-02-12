from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from rich.panel import Panel
from rich.table import Table

from .ui import format_path, rich_escape


def prompt_line(
    *,
    has_candidates: bool,
    allow_search: bool,
    allow_manual: bool,
    has_more: bool,
    allow_back: bool,
    prompt_base: str,
) -> str:
    parts: list[str] = []
    if has_candidates:
        parts.append("Enter=accept #1")
        parts.append("1-9=choose")
    if allow_search:
        parts.append("s=search")
    if allow_manual:
        parts.append("m=manual")
    parts.append("k=skip")
    parts.append("q=quit")
    if allow_back:
        parts.append("b=back")
    if has_more:
        parts.append("n=next page")
    return " | ".join(parts) if parts else prompt_base


def print_candidates(
    *,
    console: Any,
    media_type: str,
    candidates: list[Any],
    item: Any | None = None,
) -> None:
    table = Table(title="Candidates")
    table.add_column("#")
    table.add_column("Title")
    table.add_column("Year")
    show_people = False
    show_tv_fields = media_type == "tv" and item is not None
    if show_tv_fields:
        table.add_column("S/E")
        table.add_column("Episode title")
    if media_type == "movie":
        show_people = any((cand.enrichment or {}).get("director") or (cand.enrichment or {}).get("cast") for cand in candidates)
        if show_people:
            table.add_column("Director")
            table.add_column("Cast")
    table.add_column("Source")
    table.add_column("Confidence")
    for idx, cand in enumerate(candidates, start=1):
        year_text = str(cand.year) if cand.year else "Unknown"
        row = [str(idx), rich_escape(cand.title), year_text]
        if show_tv_fields:
            season = item.season if item.season is not None else "-"
            episode = item.episode if item.episode is not None else "-"
            row.append(f"{season}/{episode}")
            episode_title = cand.metadata.get("episode_title") or item.episode_title
            row.append(rich_escape(episode_title) if episode_title else "-")
        if media_type == "movie" and show_people:
            enrichment = cand.enrichment or {}
            row.append(enrichment.get("director") or "-")
            cast = enrichment.get("cast")
            if cast:
                row.append(", ".join(cast[:3]))
            else:
                row.append("-")
        row.extend([cand.source, f"{cand.confidence:.2f}"])
        table.add_row(*row)
    console.print(table)


def select_candidate(
    *,
    media_type: str,
    candidates: list[Any],
    has_more: bool,
    allow_search: bool,
    allow_manual: bool,
    allow_back: bool,
    item: Any | None,
    no_more_results_message: str,
    prompt_choice: Callable[[str, str], str],
    safe_print: Callable[[str], None],
    print_candidates_fn: Callable[[str, list[Any], Any | None], None],
    prompt_line_fn: Callable[[bool, bool, bool, bool, bool], str],
) -> Any | None | str:
    printed_table = False
    while True:
        if candidates and not printed_table:
            print_candidates_fn(media_type, candidates, item)
            printed_table = True
        safe_print(prompt_line_fn(bool(candidates), allow_search, allow_manual, has_more, allow_back))
        default_choice = "1" if candidates else ""
        choice = prompt_choice("Select", default_choice)
        if choice == "":
            if candidates:
                return candidates[0]
            safe_print("No candidates available to accept.")
            continue
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(candidates):
                return candidates[idx]
            safe_print("Invalid selection.")
            continue
        if choice == "n":
            if not has_more:
                safe_print(no_more_results_message)
                continue
            return "n"
        if choice == "b":
            if not allow_back:
                safe_print("No previous decision to return to.")
                continue
            return "b"
        if choice == "s" and allow_search:
            return "s"
        if choice == "m" and allow_manual:
            return "m"
        if choice in {"k", "q"}:
            return choice
        if allow_search:
            return f"search:{choice}"
        safe_print("Invalid choice.")


def file_panel(index: int, total: int, item: Any, incoming_root: Path | None) -> Panel:
    title_line = f"File {index}/{total} - {item.media_type.upper()} - {rich_escape(item.path.name)}"
    year_text = str(item.year) if item.year else "Unknown"
    rel_path = item.path
    if incoming_root is not None:
        try:
            rel_path = item.path.relative_to(incoming_root)
        except ValueError:
            rel_path = item.path
    lines = [
        f"Path: {format_path(rel_path)}",
        f"Detected: Title={rich_escape(item.title)}, Year={year_text}",
    ]
    if item.media_type == "tv":
        season = item.season if item.season is not None else "-"
        if item.episode is None:
            episode_text = "-"
        elif item.episode_end is not None and item.episode_end > item.episode:
            episode_text = f"{item.episode}-{item.episode_end}"
        else:
            episode_text = str(item.episode)
        lines.append(f"Season/Episode: {season}/{episode_text}")
        if item.episode_title:
            lines.append(f"Episode title: {rich_escape(item.episode_title)}")
    return Panel("\n".join(lines), title=title_line, expand=False)


def album_panel(index: int, total: int, album: Any) -> Panel:
    title_line = f"Album {index}/{total} - {rich_escape(album.source.name)}"
    lines = [
        f"Detected: Artist={rich_escape(album.artist)}, Album={rich_escape(album.album)}",
        f"Tracks: {len(album.tracks)}",
    ]
    return Panel("\n".join(lines), title=title_line, expand=False)


def print_music_candidates(*, console: Any, candidates: list[Any]) -> None:
    table = Table(title="MusicBrainz releases")
    table.add_column("#")
    table.add_column("Artist")
    table.add_column("Album")
    table.add_column("Tracks")
    table.add_column("Year")
    table.add_column("Country")
    table.add_column("MB Score")
    table.add_column("Rank Score")
    for idx, cand in enumerate(candidates, start=1):
        track_count = str(cand.track_count) if cand.track_count is not None else "-"
        year_text = str(cand.year) if cand.year else "-"
        country = cand.country or "-"
        mb_score = cand.raw_score if getattr(cand, "raw_score", None) is not None else cand.score
        table.add_row(
            str(idx),
            rich_escape(cand.artist),
            rich_escape(cand.title),
            track_count,
            year_text,
            country,
            f"{mb_score:.3f}",
            f"{cand.score:.3f}",
        )
    console.print(table)


def select_music_candidate(
    *,
    candidates: list[Any],
    prompt_choice: Callable[[str, str], str],
    safe_print: Callable[[str], None],
    print_music_candidates_fn: Callable[[list[Any]], None],
) -> Any | None | str:
    printed = False
    while True:
        if candidates and not printed:
            print_music_candidates_fn(candidates)
            printed = True
        safe_print("Enter=accept #1 | 1-9=choose | s=skip verification | q=quit")
        default_choice = "1" if candidates else ""
        choice = prompt_choice("Select", default_choice)
        if choice == "":
            if candidates:
                return candidates[0]
            return "s"
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(candidates):
                return candidates[idx]
            safe_print("Invalid selection.")
            continue
        if choice == "s":
            return "s"
        if choice == "q":
            return "q"
        safe_print("Invalid choice.")
