import os
import re
import shlex
import sys
import time
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Optional

import requests
import typer
from rapidfuzz import fuzz
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn, TimeRemainingColumn
from rich.prompt import Prompt
from rich.table import Table
from rich.tree import Tree

from .cache import Cache, NullCache
from . import music as music_util
from .executor import execute_plans
from .infer import InferredItem, infer_item
from .logging_config import configure_logging, get_logger, log_event
from .planner import plan_movie, plan_tv_show
from .paths import PathOverlapError, ensure_non_overlapping_paths, validate_non_overlapping
from .prompting import _prompt_text
from .report import write_report
from .sources import musicbrainz, tvmaze, wikidata
from .tv_episode_cache import EpisodeCache
from .undo import undo_report
from .ui import format_path, rich_escape
from .util import (
    ExecutionResult,
    MovePlan,
    build_cache_key,
    iter_video_files,
    json_dump,
    json_load,
    make_search_query,
    movie_cache_key,
    normalize_title_for_similarity,
    now_timestamp,
    tv_episode_cache_key,
    tv_show_folder_cache_key,
    tv_show_cache_key,
    unique_path,
    unique_plan_path,
)

app = typer.Typer(add_completion=True)
ASCII_UI_ENABLED = os.getenv("PLEXIFY_ASCII_UI", "").strip().lower() in {"1", "true", "yes", "on"}
console = Console(safe_box=ASCII_UI_ENABLED)
logger = get_logger(__name__)
COMPLETION_ENABLED = True
DEFAULT_EXTENSIONS = ".mkv,.mp4,.avi,.m4v,.mov,.ts"
DEFAULT_EXTENSIONS_LIST = [ext.strip() for ext in DEFAULT_EXTENSIONS.split(",") if ext.strip()]
DEFAULT_MUSIC_EXTENSIONS = "flac,mp3,m4a"
DEFAULT_MIN_CONFIDENCE = 0.90
AUTO_ACCEPT_GAP = 0.08
PROMPT_BASE = "s=search | m=manual | k=skip | q=quit"
NO_MORE_RESULTS_MESSAGE = "No more results. Try 's' to refine or 'm' to enter manually."
WIZARD_MEDIA_CHOICES = {
    "movie": "movie",
    "movies": "movie",
    "film": "movie",
    "tv": "tv",
    "show": "tv",
    "series": "tv",
    "both": "auto",
    "all": "auto",
    "auto": "auto",
}
WIZARD_MODE_CHOICES = {
    "dry-run": "dry-run",
    "dry run": "dry-run",
    "dry": "dry-run",
    "dryrun": "dry-run",
    "apply": "apply",
}
WIZARD_COPY_CHOICES = {"copy": "copy", "c": "copy", "move": "move", "m": "move"}
WIZARD_ORGANISE_CHOICES = {
    "v": "video",
    "video": "video",
    "m": "music",
    "music": "music",
}
WIZARD_LOG_LEVEL_CHOICES = {
    "debug": "DEBUG",
    "info": "INFO",
    "warning": "WARNING",
    "error": "ERROR",
}
WIZARD_LOG_FORMAT_CHOICES = {
    "text": "text",
    "json": "json",
}
LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR"}
LOG_FORMATS = {"text", "json"}
TV_SEASON_TOKEN_RE = r"(?:season|series|seaon|seson|seasn)"
TV_EXPLICIT_SEASON_RE = re.compile(rf"(?<![A-Za-z0-9]){TV_SEASON_TOKEN_RE}[-_. ]*(\d{{1,2}})(?![A-Za-z0-9])", re.IGNORECASE)
TV_EXPLICIT_SEASON_EPISODE_RE = re.compile(r"\bs\d{1,2}e\d{1,3}\b|\b\d{1,2}x\d{1,3}\b", re.IGNORECASE)
TV_SXXEYY_CAPTURE_RE = re.compile(r"\bs(\d{1,2})e\d{1,3}\b", re.IGNORECASE)
TV_XYY_CAPTURE_RE = re.compile(r"\b(\d{1,2})x\d{1,3}\b", re.IGNORECASE)


@dataclass
class Candidate:
    title: str
    year: Optional[int]
    source: str
    confidence: float
    metadata: dict[str, Any]
    enrichment: dict[str, Any] | None = None


@dataclass
class CandidatePage:
    candidates: list[Candidate]
    raw_results: list[Any] | None
    next_offset: int
    has_more: bool
    cache_hit: bool = False
    search_time: float | None = None
    fetch_time: float | None = None
    total_time: float | None = None


@dataclass
class PlanStats:
    auto_matched: int = 0
    user_confirmed: int = 0
    manual: int = 0
    skipped: int = 0
    errors: int = 0
    cache_hits: int = 0
    elapsed: float = 0.0


@dataclass
class HistoryEntry:
    index: int
    plan: MovePlan | None
    collision: bool
    cache_snapshots: list["CacheSnapshot"]
    stats_snapshot: PlanStats
    errors_len: int


@dataclass
class CacheSnapshot:
    section: str
    key: str
    previous: dict[str, Any] | None


@dataclass
class BuildCommandConfig:
    incoming: Path
    library: Path
    media_type: str
    mode: str
    copy_mode: bool
    extensions: list[str]
    min_confidence: float
    limit: int | None
    interactive: bool
    print_tree: bool
    show_enrichment: bool
    yes: bool
    no_cache: bool
    cache_file: Path | None
    clear_cache: bool
    report: Path | None
    on_conflict: str
    prune_empty_dirs: bool


@dataclass(frozen=True)
class MusicPlannedTrack:
    source: Path
    track_number: int
    track_number_text: str
    track_title: str
    track_artist: str
    ext: str
    disc_number: int | None = None


class BackRequested(Exception):
    pass


def _console_for(progress: Progress | None) -> Console:
    if progress is not None and hasattr(progress, "console"):
        return progress.console
    return console


def _parse_extensions(extensions: str) -> list[str]:
    return [ext.strip() for ext in extensions.split(",") if ext.strip()]


def _safe_print(message: str, progress: Progress | None = None) -> None:
    _console_for(progress).print(message)


def _tv_search_cache_key(query: str, year: int | None) -> str:
    year_text = str(year) if year is not None else "unknown"
    return f"{query.strip().casefold()}|{year_text}"


def _cache_entry_confirmed_or_auto(entry: dict[str, Any] | None) -> bool:
    if not entry:
        return False
    if bool(entry.get("confirmed_by_user")):
        return True
    if entry.get("selection_mode") == "auto" and not bool(entry.get("manual")):
        return True
    return False


def _media_override_key(path: Path, incoming_root: Path | None) -> str | None:
    rel = path
    if incoming_root is not None:
        try:
            rel = path.relative_to(incoming_root)
        except ValueError:
            rel = Path(path.name)
    parent = rel.parent
    if str(parent) in {"", "."}:
        return None
    return f"mediatype|{parent.as_posix().lower()}"


def _switch_item_media_type(item: InferredItem, target_media_type: str) -> InferredItem:
    if target_media_type == item.media_type:
        return item
    if target_media_type == "tv":
        fallback_title = item.path.parent.name.strip() if item.path.parent.name else ""
        switched_title = fallback_title or item.title
        return InferredItem(
            path=item.path,
            media_type="tv",
            title=switched_title,
            year=item.year,
            season=item.season,
            episode=item.episode,
            episode_title=item.episode_title,
        )
    return InferredItem(
        path=item.path,
        media_type="movie",
        title=item.title,
        year=item.year,
        season=None,
        episode=None,
        episode_title=None,
    )


def _resolve_media_type_override(
    item: InferredItem,
    cache: Cache,
    incoming_root: Path | None,
    media_type_overrides: dict[str, str] | None,
) -> tuple[InferredItem, str | None]:
    override_key = _media_override_key(item.path, incoming_root)
    if override_key is None:
        return item, None
    override_media_type = None
    if media_type_overrides is not None:
        override_media_type = media_type_overrides.get(override_key)
    if override_media_type is None:
        cached = cache.get_show(override_key)
        if cached and cached.get("confirmed_by_user"):
            cached_media_type = str(cached.get("media_type") or "").lower()
            if cached_media_type in {"movie", "tv"}:
                override_media_type = cached_media_type
                if media_type_overrides is not None:
                    media_type_overrides[override_key] = cached_media_type
    if override_media_type in {"movie", "tv"} and override_media_type != item.media_type:
        return _switch_item_media_type(item, override_media_type), override_key
    return item, override_key


def _persist_media_type_override(
    cache: Cache,
    override_key: str | None,
    media_type: str,
    media_type_overrides: dict[str, str] | None,
) -> None:
    if override_key is None:
        return
    if media_type_overrides is not None:
        media_type_overrides[override_key] = media_type
    cache.set_show(
        override_key,
        {
            "media_type": media_type,
            "confirmed_by_user": True,
            "created_at": now_timestamp(),
            "source": "MediaTypeOverride",
        },
    )
    cache.save()


def _initialise_logging(log_level: str, log_format: str, log_file: Path | None) -> None:
    level = log_level.upper()
    if level not in LOG_LEVELS:
        console.print("Invalid log level. Use DEBUG, INFO, WARNING, or ERROR.")
        raise typer.Exit(code=2)
    if log_format not in LOG_FORMATS:
        console.print("Invalid log format. Use text or json.")
        raise typer.Exit(code=2)
    configure_logging(level=level, fmt=log_format, log_file=log_file)


def _prompt_line(
    *,
    has_candidates: bool,
    allow_search: bool,
    allow_manual: bool,
    has_more: bool,
    allow_back: bool,
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
    return " | ".join(parts) if parts else PROMPT_BASE


def _build_search_query(title: str, hint: str | None) -> str:
    base = make_search_query(title) or title.strip()
    parts = [base]
    if hint:
        hint_text = hint.strip()
        if hint_text:
            parts.append(hint_text)
    return " ".join(part for part in parts if part)


def _normalize_tv_retry_query(value: str) -> str:
    cleaned = TV_EXPLICIT_SEASON_RE.sub(" ", value or "")
    return make_search_query(cleaned) or cleaned.strip()


def _extract_explicit_season_from_path(path: Path) -> int | None:
    sxxeyy = TV_SXXEYY_CAPTURE_RE.search(path.stem)
    if sxxeyy:
        return int(sxxeyy.group(1))
    xyy = TV_XYY_CAPTURE_RE.search(path.stem)
    if xyy:
        return int(xyy.group(1))
    token_match = TV_EXPLICIT_SEASON_RE.search(path.stem)
    if token_match:
        return int(token_match.group(1))
    for parent in path.parents:
        parent_match = TV_EXPLICIT_SEASON_RE.search(parent.name)
        if parent_match:
            return int(parent_match.group(1))
    return None


def _apply_tv_folder_season_lock(item: InferredItem, cache: Cache, folder_show_key: str | None) -> InferredItem:
    if folder_show_key is None or item.media_type != "tv":
        return item
    cached = cache.get_show(folder_show_key)
    if not cached or not cached.get("confirmed_by_user") or not cached.get("manual"):
        return item
    locked_season = cached.get("season")
    if locked_season is None:
        return item
    try:
        locked_season_int = int(locked_season)
    except (TypeError, ValueError):
        return item
    explicit_season = _extract_explicit_season_from_path(item.path)
    if explicit_season is not None and explicit_season != locked_season_int:
        return item
    if item.season not in {None, 1, locked_season_int}:
        return item
    return InferredItem(
        path=item.path,
        media_type=item.media_type,
        title=item.title,
        year=item.year,
        season=locked_season_int,
        episode=item.episode,
        episode_title=item.episode_title,
    )


def _with_title(item: InferredItem, title: str) -> InferredItem:
    return InferredItem(
        path=item.path,
        media_type=item.media_type,
        title=title,
        year=item.year,
        season=item.season,
        episode=item.episode,
        episode_title=item.episode_title,
    )


def _strip_outer_quotes(value: str) -> str:
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {'"', "'"}:
        return stripped[1:-1]
    return stripped


def _wizard_prefs_path() -> Path:
    return Path.home() / ".plexify" / "wizard.json"


def _load_wizard_prefs() -> dict[str, dict[str, str]]:
    path = _wizard_prefs_path()
    data = json_load(path)
    if not isinstance(data, dict):
        return {}
    cleaned: dict[str, dict[str, str]] = {}
    for key, value in data.items():
        if not isinstance(value, dict):
            continue
        cleaned[key] = {str(k): str(v) for k, v in value.items() if isinstance(k, str) and isinstance(v, str)}
    return cleaned


def _save_wizard_prefs(media_key: str, source: Path, library: Path) -> None:
    prefs = _load_wizard_prefs()
    prefs[media_key] = {"source": str(source), "library": str(library)}
    json_dump(_wizard_prefs_path(), prefs)


def _wizard_defaults(media_key: str) -> tuple[Path | None, Path | None]:
    prefs = _load_wizard_prefs()
    section = prefs.get(media_key, {})
    source = Path(section["source"]) if "source" in section else None
    library = Path(section["library"]) if "library" in section else None
    return source, library


_path_prompt_tip_shown = False
_path_prompt_fallback_tip_shown = False


def _prompt_path(prompt: str, default: str | None, *, directories_only: bool) -> str:
    global _path_prompt_tip_shown, _path_prompt_fallback_tip_shown
    is_tty = sys.stdin is not None and sys.stdin.isatty()
    if is_tty:
        try:
            from prompt_toolkit.completion import PathCompleter
            from prompt_toolkit.shortcuts import prompt as pt_prompt
        except Exception:  # noqa: BLE001
            is_tty = False
        else:
            if not _path_prompt_tip_shown:
                console.print("Tip: Tab autocompletes paths.")
                _path_prompt_tip_shown = True
            completer = PathCompleter(only_directories=directories_only, expanduser=True)
            text = pt_prompt(f"{prompt}: ", default=default or "", completer=completer)
            return _strip_outer_quotes(text)

    if not _path_prompt_fallback_tip_shown:
        console.print("Tip: install prompt_toolkit to enable in-wizard tab completion: pip install prompt_toolkit")
        _path_prompt_fallback_tip_shown = True
    return _strip_outer_quotes(Prompt.ask(prompt, default=default, show_default=default is not None))


def _prompt_choice(prompt: str, default: str, progress: Progress | None, show_default: bool = True) -> str:
    return _prompt_text(prompt, default, progress, show_default=show_default).strip().lower()


def _prompt_choice_loop(
    prompt: str,
    choices: dict[str, str],
    progress: Progress | None,
    *,
    allow_empty: bool = False,
    error: str,
    default: str | None = None,
) -> str:
    while True:
        choice = _prompt_choice(prompt, default or "", progress, show_default=default is not None)
        if choice == "" and allow_empty and default is not None:
            return default
        normalised = choices.get(choice)
        if normalised:
            return normalised
        _safe_print(error, progress)


def _confirm(prompt: str, default: bool, progress: Progress | None, show_default: bool = True) -> bool:
    default_text = "y" if default else "n"
    while True:
        choice = _prompt_choice(prompt, default_text, progress, show_default=show_default)
        if choice in {"y", "yes"}:
            return True
        if choice in {"n", "no"}:
            return False
        _safe_print("Please enter y/n.", progress)


def _prompt_int(prompt: str, default: int, progress: Progress | None) -> int:
    while True:
        value = _prompt_text(prompt, str(default), progress)
        try:
            return int(value)
        except ValueError:
            _safe_print("Please enter a whole number.", progress)


def _prompt_int_or_control(prompt: str, default: int, progress: Progress | None) -> int | str:
    while True:
        value = _prompt_text(prompt, str(default), progress)
        normalised = value.strip().lower()
        if normalised in {"k", "q"}:
            return normalised
        try:
            return int(value)
        except ValueError:
            _safe_print("Please enter a whole number.", progress)


def _print_overlap_error(exc: PathOverlapError) -> None:
    issue = exc.issue
    console.print(rich_escape(issue.reason))
    for suggestion in issue.suggestions:
        console.print(rich_escape(suggestion))


def _detect_media_in_path(path: Path, audio_exts: set[str], video_exts: set[str]) -> tuple[bool, bool]:
    has_audio = False
    has_video = False
    for base, _, files in os.walk(path):
        for name in files:
            suffix = Path(name).suffix.lower().lstrip(".")
            if suffix in audio_exts:
                has_audio = True
            if suffix in video_exts:
                has_video = True
            if has_audio and has_video:
                return True, True
    return has_audio, has_video


def _confirm_move(progress: Progress | None) -> bool:
    phrase = _prompt_text("To proceed, type MOVE", "", progress, show_default=False)
    return phrase.strip().lower() == "move"


def _compact_text(value: str) -> str:
    return re.sub(r"\s+", "", value)


def _compact_sequel_form(value: str) -> str | None:
    tokens = value.split()
    if len(tokens) < 2:
        return None
    if len(tokens[0]) == 1 and tokens[-1].isdigit():
        return f"{tokens[0]}{tokens[-1]}"
    return None


def _title_similarity(title_guess: str, title_actual: str) -> float:
    norm_left = normalize_title_for_similarity(title_guess) or title_guess.lower()
    norm_right = normalize_title_for_similarity(title_actual) or title_actual.lower()
    search_left = make_search_query(title_guess) or title_guess.lower()
    search_right = make_search_query(title_actual) or title_actual.lower()
    forms_left = {norm_left, _compact_text(norm_left), search_left, _compact_text(search_left)}
    forms_right = {norm_right, _compact_text(norm_right), search_right, _compact_text(search_right)}
    compact_left = _compact_sequel_form(norm_left)
    compact_right = _compact_sequel_form(norm_right)
    if compact_left:
        forms_left.add(compact_left)
    if compact_right:
        forms_right.add(compact_right)
    best = 0.0
    for left in forms_left:
        for right in forms_right:
            score = max(
                fuzz.WRatio(left, right),
                fuzz.partial_ratio(left, right),
            ) / 100.0
            if score > best:
                best = score
    return best


def _year_adjustment(target_year: int | None, candidate_year: int | None) -> float:
    if not target_year or not candidate_year:
        return 0.0
    diff = abs(target_year - candidate_year)
    if diff == 0:
        return 0.20
    if diff == 1:
        return 0.08
    if diff == 2:
        return 0.04
    return -min(0.30, 0.03 * diff)


def _confidence_score(title_guess: str, title_actual: str, year_guess: int | None, year_actual: int | None) -> float:
    base = _title_similarity(title_guess, title_actual)
    adjusted = base + _year_adjustment(year_guess, year_actual)
    return max(0.0, min(1.0, adjusted))


def _tv_confidence_score(title_guess: str, title_actual: str, year_guess: int | None, year_actual: int | None) -> float:
    base = _title_similarity(title_guess, title_actual)
    if not year_guess or not year_actual:
        return max(0.0, min(1.0, base))
    diff = abs(year_guess - year_actual)
    if diff == 0:
        adjustment = 0.35
    elif diff <= 1:
        adjustment = 0.18
    elif diff <= 2:
        adjustment = 0.10
    elif diff <= 5:
        adjustment = -0.08 * diff
    elif diff <= 10:
        adjustment = -0.35
    else:
        adjustment = -0.6
    return max(0.0, min(1.0, base + adjustment))


def _year_distance(target_year: int | None, candidate_year: int | None) -> int:
    if not target_year or not candidate_year:
        return 999
    return abs(target_year - candidate_year)


def _has_sequel_marker(title: str) -> bool:
    tokens = re.split(r"[.\s_\-:/\\]+", title.strip())
    if not tokens:
        return False
    last = tokens[-1].lower()
    if last in {"i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x", "xi", "xii", "xiii", "xiv", "xv"}:
        return True
    return bool(re.fullmatch(r"\d+", last))


def _search_lost_sequel_marker(title: str, search_query: str) -> bool:
    if not _has_sequel_marker(title):
        return False
    return not _has_sequel_marker(search_query)


def _format_value(value: str | None) -> str:
    return value if value else "-"


def _format_names(names: list[str] | None, limit: int = 3) -> str:
    if not names:
        return "-"
    return ", ".join(names[:limit])


def _maybe_enrich_candidates(
    media_type: str,
    candidates: list[Candidate],
    session_tv: requests.Session,
    session_wd: requests.Session,
    cache: Cache,
    interactive: bool,
) -> None:
    if not interactive or not candidates:
        return
    timeout = (2, 5)
    updated = False
    for cand in candidates[:3]:
        if cand.enrichment is not None:
            continue
        if media_type == "movie" and cand.source == "Wikidata":
            qid = cand.metadata.get("qid")
            if not qid:
                cand.enrichment = {}
                continue
            cache_key = f"wikidata:{qid}"
            cached = cache.get_enrichment(cache_key)
            if cached is not None:
                cand.enrichment = cached
                continue
            details = wikidata.fetch_enrichment(str(qid), session=session_wd, timeout=timeout)
            if details:
                cache.set_enrichment(cache_key, details)
                cand.enrichment = details
                updated = True
            else:
                cand.enrichment = {}
        if media_type == "tv" and cand.source == "TVMaze":
            show_id = cand.metadata.get("id")
            if not show_id:
                cand.enrichment = {}
                continue
            cache_key = f"tvmaze:{show_id}"
            cached = cache.get_enrichment(cache_key)
            if cached is not None:
                cand.enrichment = cached
                continue
            details = tvmaze.fetch_show_details(int(show_id), session=session_tv, timeout=timeout)
            if details:
                enrichment = {"network": details.network, "creator": details.creator, "cast": details.cast}
                cache.set_enrichment(cache_key, enrichment)
                cand.enrichment = enrichment
                updated = True
            else:
                cand.enrichment = {}
    if updated:
        cache.save()


def _print_candidates(
    media_type: str,
    candidates: list[Candidate],
    progress: Progress | None = None,
    *,
    item: InferredItem | None = None,
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
        show_people = any(
            (cand.enrichment or {}).get("director") or (cand.enrichment or {}).get("cast") for cand in candidates
        )
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
            row.append(_format_value(enrichment.get("director")))
            row.append(_format_names(enrichment.get("cast")))
        row.extend([cand.source, f"{cand.confidence:.2f}"])
        table.add_row(*row)
    _console_for(progress).print(table)


def _select_candidate(
    media_type: str,
    candidates: list[Candidate],
    progress: Progress | None,
    has_more: bool,
    *,
    allow_search: bool,
    allow_manual: bool,
    allow_back: bool,
    item: InferredItem | None = None,
) -> Candidate | None | str:
    printed_table = False
    while True:
        if candidates and not printed_table:
            _print_candidates(media_type, candidates, progress, item=item)
            printed_table = True
        _safe_print(
            _prompt_line(
                has_candidates=bool(candidates),
                allow_search=allow_search,
                allow_manual=allow_manual,
                has_more=has_more,
                allow_back=allow_back,
            ),
            progress,
        )
        default_choice = "1" if candidates else ""
        choice = _prompt_choice("Select", default_choice, progress, show_default=False)
        if choice == "":
            if candidates:
                return candidates[0]
            _safe_print("No candidates available to accept.", progress)
            continue
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(candidates):
                return candidates[idx]
            _safe_print("Invalid selection.", progress)
            continue
        if choice == "n":
            if not has_more:
                _safe_print(NO_MORE_RESULTS_MESSAGE, progress)
                continue
            return "n"
        if choice == "b":
            if not allow_back:
                _safe_print("No previous decision to return to.", progress)
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
        _safe_print("Invalid choice.", progress)


def _tv_candidates(
    item: InferredItem,
    session: requests.Session,
    cache: Cache,
    show_cache: bool,
    *,
    incoming_root: Path | None = None,
    cache_key: str | None = None,
    offset: int = 0,
    raw_results: list[tvmaze.TVMazeShow] | None = None,
    search_query: str | None = None,
    progress: Progress | None = None,
    limit: int = 5,
    offline: bool = False,
    interactive: bool = False,
    search_cache: dict[str, list[tvmaze.TVMazeShow]] | None = None,
) -> CandidatePage:
    path_key = cache_key or item.title
    reusable_show_key = tv_show_cache_key(item.title, item.year) if _reusable_tv_cache_safe(item) else None
    reusable_episode_key = None
    folder_show_key = tv_show_folder_cache_key(item.path, incoming_root)
    if item.season is not None and item.episode is not None:
        reusable_episode_key = tv_episode_cache_key(item.title, item.year, item.season, item.episode)
    cached = None
    cached_key = None
    if reusable_episode_key:
        cached = cache.get_show(reusable_episode_key)
        cached_key = reusable_episode_key if cached else None
    if cached is None and reusable_show_key:
        cached = cache.get_show(reusable_show_key)
        cached_key = reusable_show_key if cached else None
    if cached is None and folder_show_key:
        cached = cache.get_show(folder_show_key)
        cached_key = folder_show_key if cached else None
    if cached is None:
        cached = cache.get_show(path_key)
        cached_key = path_key if cached else None
    results: list[Candidate] = []
    elapsed = 0.0
    total_time = None
    if cached:
        if not _cache_entry_confirmed_or_auto(cached):
            cached = None
        elif not cached.get("manual") and not _cache_entry_compatible(item.year, cached.get("premiered")):
            cached = None
    if cached:
        log_event(
            logger,
            "cache_hit",
            cache_scope="tv",
            cache_key=cached_key,
            path=item.path,
            media_type=item.media_type,
        )
        if show_cache:
            name = cached.get("name") or item.title
            year = cached.get("chosen_year") or cached.get("premiered")
            year_text = f" ({year})" if year else ""
            _safe_print("Cache hit.", progress)
            if cached_key == reusable_show_key:
                _safe_print("Cache type: REUSABLE", progress)
                _safe_print(
                    f"Using cached show match: {name}{year_text} [TVMaze]. Using inferred S/E for this file.",
                    progress,
                )
            elif cached_key == folder_show_key:
                _safe_print("Cache type: FOLDER", progress)
                _safe_print(
                    f"Using cached show match for folder: {name}{year_text} [TVMaze]. Using inferred S/E for this file.",
                    progress,
                )
            else:
                _safe_print("Cache type: FILE-SPECIFIC", progress)
                _safe_print(
                    f"Using cached match for: {rich_escape(item.path.name)} -> {rich_escape(name)}{year_text} [TVMaze]",
                    progress,
                )
        if cached.get("manual"):
            metadata: dict[str, Any] = {
                "id": None,
                "name": cached.get("name") or item.title,
                "year": cached.get("chosen_year") or cached.get("premiered"),
                "manual": True,
            }
            if cached_key == folder_show_key and cached.get("season") is not None:
                metadata["season"] = cached.get("season")
            if cached_key not in {reusable_show_key, folder_show_key}:
                if "season" in cached:
                    metadata["season"] = cached.get("season")
                if "episode" in cached:
                    metadata["episode"] = cached.get("episode")
                if "episode_title" in cached:
                    metadata["episode_title"] = cached.get("episode_title")
            candidate = Candidate(
                title=metadata["name"],
                year=metadata.get("year"),
                source="Manual",
                confidence=1.0,
                metadata=metadata,
            )
        else:
            show = tvmaze.TVMazeShow(id=int(cached["id"]), name=cached["name"], premiered=cached.get("premiered"))
            candidate = _tv_candidate_from_show(item, show)
        if cached_key not in {reusable_show_key, folder_show_key}:
            candidate.metadata["season"] = cached.get("season")
            candidate.metadata["episode"] = cached.get("episode")
            candidate.metadata["episode_title"] = cached.get("episode_title")
        results.append(candidate)
        return CandidatePage(candidates=results, raw_results=None, next_offset=0, has_more=False, cache_hit=True)

    if offline:
        log_event(
            logger,
            "offline_no_cached_match",
            media_type=item.media_type,
            path=item.path,
            title=item.title,
        )
        return CandidatePage(candidates=[], raw_results=[], next_offset=0, has_more=False)

    if raw_results is None:
        query = search_query or make_search_query(item.title) or item.title
        cache_lookup_key = _tv_search_cache_key(query, item.year)
        if search_cache is not None and cache_lookup_key in search_cache:
            raw_results = search_cache[cache_lookup_key]
            elapsed = 0.0
            total_time = 0.0
        else:
            log_event(
                logger,
                "candidate_search_started",
                source="TVMaze",
                query=query,
                media_type=item.media_type,
                path=item.path,
            )
            _safe_print(f"Searching TVMaze for: {rich_escape(query)}", progress)
            total_started = time.monotonic()
            started = total_started
            raw_results = tvmaze.search_shows(query, session=session, raise_on_error=interactive)
            elapsed = time.monotonic() - started
            total_time = time.monotonic() - total_started
            if not raw_results:
                retry_query = _normalize_tv_retry_query(search_query or item.title)
                if retry_query and retry_query != query:
                    _safe_print(f"Retrying TVMaze with normalized query: {rich_escape(retry_query)}", progress)
                    started_retry = time.monotonic()
                    raw_results = tvmaze.search_shows(retry_query, session=session, raise_on_error=interactive)
                    elapsed += time.monotonic() - started_retry
                    total_time = time.monotonic() - total_started
                    cache_lookup_key = _tv_search_cache_key(retry_query, item.year)
            log_event(
                logger,
                "candidate_search_finished",
                source="TVMaze",
                query=query,
                media_type=item.media_type,
                path=item.path,
                result_count=len(raw_results),
                duration_ms=int(total_time * 1000),
            )
            if search_cache is not None:
                search_cache[cache_lookup_key] = raw_results
            if not raw_results:
                _safe_print(f"No candidates (api={elapsed:.2f}s).", progress)
                return CandidatePage(
                    candidates=[],
                    raw_results=raw_results,
                    next_offset=0,
                    has_more=False,
                    search_time=elapsed,
                    total_time=total_time,
                )
    page = raw_results[offset : offset + limit]
    for show in page:
        results.append(_tv_candidate_from_show(item, show))
    results.sort(key=lambda cand: (-cand.confidence, _year_distance(item.year, cand.year)))
    next_offset = offset + limit
    has_more = next_offset < len(raw_results)
    if raw_results is not None and offset == 0:
        best = results[0].confidence if results else 0.0
        total_text = f"{total_time:.2f}s" if total_time is not None else f"{elapsed:.2f}s"
        _safe_print(
            f"Found {len(results)} candidates (best confidence {best:.2f}, api={elapsed:.2f}s, total={total_text}).",
            progress,
        )
    return CandidatePage(candidates=results, raw_results=raw_results, next_offset=next_offset, has_more=has_more)


def _tv_candidate_from_show(item: InferredItem, show: tvmaze.TVMazeShow) -> Candidate:
    year = None
    premiered = show.premiered
    if isinstance(premiered, int):
        year = premiered
    elif isinstance(premiered, str):
        match = re.match(r"(\d{4})", premiered)
        if match:
            year = int(match.group(1))
    confidence = _tv_confidence_score(item.title, show.name, item.year, year)
    metadata: dict[str, Any] = {"id": show.id, "name": show.name, "year": year}

    return Candidate(title=show.name, year=year, source="TVMaze", confidence=confidence, metadata=metadata)


def _maybe_fetch_episode_title(
    item: InferredItem,
    candidate: Candidate,
    session: requests.Session,
    episode_cache: EpisodeCache,
    *,
    bump_confidence: bool,
) -> None:
    if item.season is None or item.episode is None:
        return
    if candidate.metadata.get("manual"):
        return
    if "episode_title" in candidate.metadata:
        return
    show_id = candidate.metadata.get("id")
    if not show_id:
        return
    episodes = episode_cache.get_episodes(int(show_id), session=session)
    episode_title = None
    for ep in episodes:
        if ep.season == item.season and ep.number == item.episode:
            episode_title = ep.name
            break
    candidate.metadata["episode_title"] = episode_title
    if episode_title and bump_confidence:
        candidate.confidence = min(1.0, candidate.confidence + 0.1)


def _resolve_episode_from_title(
    item: InferredItem,
    show_id: int | None,
    session: requests.Session,
    episode_cache: EpisodeCache,
    progress: Progress | None,
) -> tuple[int, int, str | None] | None:
    if not item.episode_title or show_id is None:
        return None
    episodes = episode_cache.get_episodes(int(show_id), session=session)
    if not episodes:
        return None
    scored: list[tuple[float, tvmaze.TVMazeEpisode]] = []
    for ep in episodes:
        if not ep.name:
            continue
        score = fuzz.WRatio(item.episode_title, ep.name) / 100.0
        scored.append((score, ep))
    if not scored:
        return None
    scored.sort(key=lambda row: row[0], reverse=True)
    top = scored[:5]
    table = Table(title="Episode matches")
    table.add_column("#")
    table.add_column("Season")
    table.add_column("Episode")
    table.add_column("Title")
    table.add_column("Score")
    for idx, (score, ep) in enumerate(top, start=1):
        table.add_row(str(idx), str(ep.season), str(ep.number), ep.name, f"{score:.2f}")
    _console_for(progress).print(table)
    while True:
        _safe_print("Enter=accept #1 | 1-5=choose | m=manual | k=skip", progress)
        choice = _prompt_choice("Select episode", "", progress, show_default=False)
        if choice == "":
            score, ep = top[0]
            return ep.season, ep.number, ep.name
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(top):
                score, ep = top[idx]
                return ep.season, ep.number, ep.name
            _safe_print("Invalid selection.", progress)
            continue
        if choice == "m":
            return None
        if choice == "k":
            return None
        _safe_print("Invalid choice.", progress)


def _movie_candidates(
    item: InferredItem,
    session: requests.Session,
    cache: Cache,
    show_cache: bool,
    *,
    cache_key: str | None = None,
    offset: int = 0,
    raw_results: list[wikidata.WikidataCandidate] | None = None,
    search_query: str | None = None,
    progress: Progress | None = None,
    limit: int = 5,
    offline: bool = False,
    interactive: bool = False,
) -> CandidatePage:
    path_key = cache_key or item.title
    reusable_key = movie_cache_key(item.title, item.year)
    cached = None
    cached_key = None
    if _reusable_movie_cache_safe(item):
        cached = cache.get_movie(reusable_key)
        cached_key = reusable_key if cached else None
    if cached is None:
        cached = cache.get_movie(path_key)
        cached_key = path_key if cached else None
    results: list[Candidate] = []
    elapsed = 0.0
    fetch_time = 0.0
    total_time = None
    if cached and not cached.get("manual"):
        if not _cache_entry_confirmed_or_auto(cached):
            cached = None
        elif not _cache_entry_compatible(item.year, cached.get("year")):
            cached = None
    if cached and not cached.get("manual"):
        log_event(
            logger,
            "cache_hit",
            cache_scope="movie",
            cache_key=cached_key,
            path=item.path,
            media_type=item.media_type,
        )
        if show_cache:
            title = cached.get("title") or item.title
            year = cached.get("year")
            year_text = f" ({year})" if year else ""
            _safe_print("Cache hit.", progress)
            if cached_key == reusable_key:
                _safe_print("Cache type: REUSABLE", progress)
            else:
                _safe_print("Cache type: FILE-SPECIFIC", progress)
            _safe_print(
                f"Using cached match for: {rich_escape(item.path.name)} -> {rich_escape(title)}{year_text} [Wikidata]",
                progress,
            )
        film = wikidata.WikidataFilm(qid=cached["qid"], title=cached["title"], year=cached.get("year"), is_film=True)
        results.append(_movie_candidate_from_film(item, film))
        return CandidatePage(candidates=results, raw_results=None, next_offset=0, has_more=False, cache_hit=True)

    if offline:
        log_event(
            logger,
            "offline_no_cached_match",
            media_type=item.media_type,
            path=item.path,
            title=item.title,
        )
        return CandidatePage(candidates=[], raw_results=[], next_offset=0, has_more=False)

    if raw_results is None:
        query = search_query or make_search_query(item.title) or item.title
        log_event(
            logger,
            "candidate_search_started",
            source="Wikidata",
            query=query,
            media_type=item.media_type,
            path=item.path,
        )
        _safe_print(f"Searching Wikidata for: {rich_escape(query)}", progress)
        total_started = time.monotonic()
        started = total_started
        raw_results = wikidata.search(query, session=session, limit=10, raise_on_error=interactive)
        elapsed = time.monotonic() - started
        if not raw_results:
            total_time = time.monotonic() - total_started
            _safe_print(f"No candidates (api={total_time:.2f}s).", progress)
            return CandidatePage(
                candidates=[],
                raw_results=raw_results,
                next_offset=0,
                has_more=False,
                search_time=elapsed,
                total_time=total_time,
            )
        total_time = time.monotonic() - total_started
        log_event(
            logger,
            "candidate_search_finished",
            source="Wikidata",
            query=query,
            media_type=item.media_type,
            path=item.path,
            result_count=len(raw_results),
            duration_ms=int(total_time * 1000),
        )
        total_started = time.monotonic()
    idx = offset
    fetch_started = time.monotonic()
    while idx < len(raw_results) and len(results) < limit:
        cand = raw_results[idx]
        idx += 1
        film = wikidata.fetch_entity(cand.qid, session=session)
        if not film.is_film:
            continue
        results.append(_movie_candidate_from_film(item, film, description=cand.description))
    fetch_time = time.monotonic() - fetch_started
    results.sort(key=lambda cand: (-cand.confidence, _year_distance(item.year, cand.year)))
    has_more = idx < len(raw_results)
    if raw_results is not None and offset == 0:
        best = results[0].confidence if results else 0.0
        if total_time is None:
            total_time = elapsed + fetch_time
        else:
            total_time = total_time + fetch_time
        _safe_print(
            f"Found {len(results)} candidates (best confidence {best:.2f}, "
            f"api={elapsed:.2f}s, fetch={fetch_time:.2f}s, total={total_time:.2f}s).",
            progress,
        )
    return CandidatePage(
        candidates=results,
        raw_results=raw_results,
        next_offset=idx,
        has_more=has_more,
        search_time=elapsed,
        fetch_time=fetch_time,
        total_time=total_time,
    )


def _movie_candidate_from_film(
    item: InferredItem,
    film: wikidata.WikidataFilm,
    *,
    description: str | None = None,
) -> Candidate:
    confidence = _confidence_score(item.title, film.title, item.year, film.year)
    metadata = {"qid": film.qid, "title": film.title, "year": film.year, "description": description}
    return Candidate(title=film.title, year=film.year, source="Wikidata", confidence=confidence, metadata=metadata)


def _prompt_manual_tv(item: InferredItem, progress: Progress | None) -> Candidate:
    show_name = _prompt_text("Show name", item.title, progress)
    year_text = _prompt_text("Show year", str(item.year) if item.year else "", progress)
    season_text = _prompt_text("Season", str(item.season) if item.season else "1", progress)
    episode_text = _prompt_text("Episode", str(item.episode) if item.episode else "1", progress)
    episode_title = _prompt_text("Episode title", item.episode_title or "", progress)
    year = int(year_text) if year_text else None
    season = int(season_text)
    episode = int(episode_text)
    metadata = {
        "id": None,
        "name": show_name,
        "year": year,
        "season": season,
        "episode": episode,
        "episode_title": episode_title or None,
        "manual": True,
    }
    return Candidate(title=show_name, year=year, source="Manual", confidence=1.0, metadata=metadata)


def _prompt_manual_movie(item: InferredItem, progress: Progress | None) -> tuple[Candidate, str]:
    title = _prompt_text("Movie title", item.title, progress)
    year_text = _prompt_text("Movie year (optional, helps disambiguate)", "", progress, show_default=False)
    hint = _prompt_text("Hint (optional, director/cast/keyword)", "", progress, show_default=False)
    year = int(year_text) if year_text else None
    metadata = {"qid": None, "title": title, "year": year, "manual": True}
    return Candidate(title=title, year=year, source="Manual", confidence=1.0, metadata=metadata), hint


def _prompt_search(item: InferredItem, progress: Progress | None) -> tuple[InferredItem, str]:
    query = _prompt_text("Search query", item.title, progress)
    hint = _prompt_text("Hint (optional, director/cast/keyword)", "", progress, show_default=False)
    return _with_title(item, query), _build_search_query(query, hint)


def _record_stat(stats: PlanStats | None, outcome: str) -> None:
    if stats is None:
        return
    if outcome == "auto":
        stats.auto_matched += 1
    elif outcome == "confirmed":
        stats.user_confirmed += 1
    elif outcome == "manual":
        stats.manual += 1
    elif outcome == "skipped":
        stats.skipped += 1


def _record_cache_hit(stats: PlanStats | None) -> None:
    if stats is None:
        return
    stats.cache_hits += 1


def _snapshot_stats(stats: PlanStats) -> PlanStats:
    return PlanStats(
        auto_matched=stats.auto_matched,
        user_confirmed=stats.user_confirmed,
        manual=stats.manual,
        skipped=stats.skipped,
        errors=stats.errors,
        cache_hits=stats.cache_hits,
        elapsed=stats.elapsed,
    )


def _cache_entry_compatible(inferred_year: int | None, cached_year: int | None) -> bool:
    if inferred_year is None or cached_year is None:
        return True
    return _year_distance(inferred_year, cached_year) <= 2


def _is_ambiguous_cache_title(title: str) -> bool:
    normalised = normalize_title_for_similarity(title)
    tokens = [token for token in re.split(r"\s+", normalised) if token]
    if not tokens:
        return True
    if len(tokens) == 1:
        return True
    if len(normalised) < 6:
        return True
    generic_titles = {"movie", "film", "show", "series", "episode", "unknown", "sample", "video", "tv"}
    if normalised in generic_titles:
        return True
    return False


def _reusable_cache_safe(title: str, year: int | None) -> bool:
    if year is not None:
        return True
    return not _is_ambiguous_cache_title(title)


def _reusable_movie_cache_safe(item: InferredItem) -> bool:
    return _reusable_cache_safe(item.title, item.year)


def _reusable_tv_cache_safe(item: InferredItem) -> bool:
    return _reusable_cache_safe(item.title, item.year)


def _auto_acceptable(
    candidates: list[Candidate],
    min_confidence: float,
    *,
    title: str,
    search_query: str,
    target_year: int | None,
) -> bool:
    if not candidates:
        return False
    if candidates[0].confidence < min_confidence:
        return False
    if _search_lost_sequel_marker(title, search_query):
        return False
    if len(candidates) == 1:
        return True
    gap = candidates[0].confidence - candidates[1].confidence
    if gap >= AUTO_ACCEPT_GAP:
        return True
    if _year_distance(target_year, candidates[0].year) <= 2:
        return True
    return False


def _resolve_destination(
    destination: Path,
    on_conflict: str,
    planned: dict[str, int] | None,
    progress: Progress | None,
) -> tuple[Path | None, bool]:
    changed = False
    if destination.exists():
        if on_conflict == "skip":
            _safe_print(f"Skipping due to existing destination: {format_path(destination)}", progress)
            return None, False
        if on_conflict == "rename":
            destination = unique_path(destination)
            changed = True
    if planned is None:
        planned = {}
    destination, planned_changed = unique_plan_path(destination, planned)
    changed = changed or planned_changed
    return destination, changed


def _file_panel(index: int, total: int, item: InferredItem, incoming_root: Path | None) -> Panel:
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
        episode = item.episode if item.episode is not None else "-"
        lines.append(f"Season/Episode: {season}/{episode}")
        if item.episode_title:
            lines.append(f"Episode title: {rich_escape(item.episode_title)}")
    return Panel("\n".join(lines), title=title_line, expand=False)


def _album_panel(index: int, total: int, album: music_util.AlbumGroup) -> Panel:
    title_line = f"Album {index}/{total} - {rich_escape(album.source.name)}"
    lines = [
        f"Detected: Artist={rich_escape(album.artist)}, Album={rich_escape(album.album)}",
        f"Tracks: {len(album.tracks)}",
    ]
    return Panel("\n".join(lines), title=title_line, expand=False)


def _print_music_candidates(candidates: list[musicbrainz.ReleaseCandidate]) -> None:
    table = Table(title="MusicBrainz releases")
    table.add_column("#")
    table.add_column("Artist")
    table.add_column("Album")
    table.add_column("Tracks")
    table.add_column("Year")
    table.add_column("Country")
    table.add_column("Confidence")
    for idx, cand in enumerate(candidates, start=1):
        track_count = str(cand.track_count) if cand.track_count is not None else "-"
        year_text = str(cand.year) if cand.year else "-"
        country = cand.country or "-"
        table.add_row(
            str(idx),
            rich_escape(cand.artist),
            rich_escape(cand.title),
            track_count,
            year_text,
            country,
            f"{cand.score:.2f}",
        )
    console.print(table)


def _rank_music_candidates(
    candidates: list[musicbrainz.ReleaseCandidate],
    track_count: int,
) -> list[musicbrainz.ReleaseCandidate]:
    ranked: list[musicbrainz.ReleaseCandidate] = []
    for cand in candidates:
        bonus = 0.0
        if cand.track_count is not None:
            diff = abs(cand.track_count - track_count)
            if diff == 0:
                bonus = 0.20
            elif diff == 1:
                bonus = 0.10
            elif diff >= 5:
                bonus = -0.05
        adjusted = min(1.0, max(0.0, cand.score + bonus))
        ranked.append(replace(cand, score=adjusted))
    ranked.sort(key=lambda candidate: candidate.score, reverse=True)
    return ranked


def _select_music_candidate(
    candidates: list[musicbrainz.ReleaseCandidate],
) -> musicbrainz.ReleaseCandidate | None | str:
    printed = False
    while True:
        if candidates and not printed:
            _print_music_candidates(candidates)
            printed = True
        _safe_print("Enter=accept #1 | 1-9=choose | s=skip verification | q=quit", None)
        default_choice = "1" if candidates else ""
        choice = _prompt_choice("Select", default_choice, None, show_default=False)
        if choice == "":
            if candidates:
                return candidates[0]
            return "s"
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(candidates):
                return candidates[idx]
            _safe_print("Invalid selection.", None)
            continue
        if choice == "s":
            return "s"
        if choice == "q":
            return "q"
        _safe_print("Invalid choice.", None)


def _music_tracks_from_filenames(tracks: list[music_util.TrackInfo]) -> list[MusicPlannedTrack]:
    planned: list[MusicPlannedTrack] = []
    for track in tracks:
        multi_disc = track.track_number >= 100
        track_number_text = music_util.format_track_number(track.track_number, multi_disc=multi_disc)
        planned.append(
            MusicPlannedTrack(
                source=track.source,
                track_number=track.track_number,
                track_number_text=track_number_text,
                track_title=track.track_title,
                track_artist=track.track_artist,
                ext=track.ext,
            )
        )
    return planned


def _map_musicbrainz_tracks(
    tracks: list[music_util.TrackInfo],
    mb_tracks: list[musicbrainz.Track],
) -> tuple[list[MusicPlannedTrack] | None, str | None]:
    if not tracks or not mb_tracks:
        return None, "Missing tracks to map"
    if len(tracks) != len(mb_tracks):
        return None, "Track count mismatch"
    disc_numbers = {track.disc for track in mb_tracks}
    disc_count = len(disc_numbers)
    has_multiple_discs = disc_count > 1 or any(disc > 1 for disc in disc_numbers)
    use_disc_numbers = any(track.track_number >= 100 for track in tracks)

    if use_disc_numbers:
        input_map: dict[tuple[int, int], music_util.TrackInfo] = {}
        for track in tracks:
            disc = track.track_number // 100
            number = track.track_number % 100
            if disc <= 0 or number <= 0:
                return None, "Invalid disc-style track numbering"
            key = (disc, number)
            if key in input_map:
                return None, "Duplicate disc-style track numbers"
            input_map[key] = track
        mapped: list[MusicPlannedTrack] = []
        for mb_track in mb_tracks:
            key = (mb_track.disc, mb_track.number)
            source_track = input_map.get(key)
            if source_track is None:
                return None, "Missing disc/track matches"
            track_number_text = music_util.format_track_number(
                mb_track.number,
                disc_number=mb_track.disc,
                multi_disc=disc_count > 1,
            )
            mapped.append(
                MusicPlannedTrack(
                    source=source_track.source,
                    track_number=mb_track.disc * 100 + mb_track.number if disc_count > 1 else mb_track.number,
                    track_number_text=track_number_text,
                    track_title=mb_track.title,
                    track_artist=source_track.track_artist,
                    ext=source_track.ext,
                    disc_number=mb_track.disc,
                )
            )
        return mapped, None

    if has_multiple_discs:
        return None, "Multi-disc release without disc numbers in filenames"

    input_by_number: dict[int, music_util.TrackInfo] = {}
    for track in tracks:
        if track.track_number in input_by_number:
            return None, "Duplicate track numbers in filenames"
        input_by_number[track.track_number] = track
    mapped: list[MusicPlannedTrack] = []
    for mb_track in mb_tracks:
        source_track = input_by_number.get(mb_track.number)
        if source_track is None:
            return None, "Missing track numbers in filenames"
        track_number_text = music_util.format_track_number(mb_track.number)
        mapped.append(
            MusicPlannedTrack(
                source=source_track.source,
                track_number=mb_track.number,
                track_number_text=track_number_text,
                track_title=mb_track.title,
                track_artist=source_track.track_artist,
                ext=source_track.ext,
                disc_number=mb_track.disc,
            )
        )
    return mapped, None


def _map_musicbrainz_by_order(
    tracks: list[music_util.TrackInfo],
    mb_tracks: list[musicbrainz.Track],
) -> list[MusicPlannedTrack]:
    sorted_tracks = sorted(tracks, key=lambda track: (track.track_number, track.source.name.lower()))
    sorted_mb = sorted(mb_tracks, key=lambda track: (track.disc, track.number))
    disc_count = len({track.disc for track in sorted_mb})
    mapped: list[MusicPlannedTrack] = []
    for source_track, mb_track in zip(sorted_tracks, sorted_mb, strict=False):
        track_number_text = music_util.format_track_number(
            mb_track.number,
            disc_number=mb_track.disc,
            multi_disc=disc_count > 1,
        )
        mapped.append(
            MusicPlannedTrack(
                source=source_track.source,
                track_number=mb_track.disc * 100 + mb_track.number if disc_count > 1 else mb_track.number,
                track_number_text=track_number_text,
                track_title=mb_track.title,
                track_artist=source_track.track_artist,
                ext=source_track.ext,
                disc_number=mb_track.disc,
            )
        )
    return mapped


def _should_use_various_artists(album: music_util.AlbumGroup, candidate_artist: str | None) -> bool:
    if candidate_artist and candidate_artist.strip().lower() == "various artists":
        return True
    if album.artist.strip().lower() in {"various artists", "va"}:
        return True
    unique_artists = {track.track_artist.strip().lower() for track in album.tracks if track.track_artist.strip()}
    return len(unique_artists) > 1


def _print_music_album_summary(
    *,
    album_dest: Path,
    track_count: int,
    artwork: bool,
    cue_count: int,
    log_count: int,
) -> None:
    console.print(f"Album destination: {format_path(album_dest)}")
    console.print(f"Tracks: {track_count}")
    if artwork:
        console.print("Artwork: cover.jpg")
    if cue_count:
        console.print(f"CUE files: {cue_count}")
    if log_count:
        console.print(f"LOG files: {log_count}")


def _print_plan(plan: MovePlan, progress: Progress | None = None) -> None:
    lines = [f"FROM: {format_path(plan.source)}", f"TO:   {format_path(plan.destination)}"]
    _safe_print(Panel("\n".join(lines), title="Plan", style="cyan", expand=False), progress)


def _print_choice(selected: Candidate, progress: Progress | None = None) -> None:
    year_text = str(selected.year) if selected.year else "Unknown"
    _safe_print(f"Chosen: {rich_escape(selected.title)} ({year_text}) from {selected.source}", progress)


def _fetch_with_retry(
    label: str,
    fetch_fn: Callable[[], Any],
    interactive: bool,
    progress: Progress | None,
) -> Any:
    while True:
        try:
            return fetch_fn()
        except requests.RequestException as exc:
            _safe_print(f"{label} request failed: {exc.__class__.__name__}", progress)
            if not interactive:
                raise
            retry = _prompt_choice("Retry? [Y/n]", "y", progress)
            if retry in {"y", "yes"}:
                continue
            return None


def _build_tree(paths: list[Path]) -> Tree:
    tree = Tree("Planned destinations")
    root_map: dict[Tree, dict[str, Tree]] = {}
    for path in sorted(paths):
        current = tree
        for part in path.parts:
            if current not in root_map:
                root_map[current] = {}
            current_children = root_map[current]
            safe_part = rich_escape(part)
            child = current_children.get(safe_part)
            if child is None:
                child = current.add(safe_part)
                current_children[safe_part] = child
            current = child
    return tree


def _apply_with_progress(plans: list[MovePlan], copy_mode: bool, on_conflict: str) -> ExecutionResult:
    if not plans:
        return execute_plans(plans, apply=True, copy_mode=copy_mode, on_conflict=on_conflict)
    action = "Copying" if copy_mode else "Moving"
    with Progress(
        BarColumn(),
        TextColumn("{task.completed}/{task.total} - {task.description}"),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
    ) as progress:
        task = progress.add_task("Preparing...", total=len(plans))

        def _on_progress(completed: int, total: int, plan: MovePlan) -> None:
            description = f"{action}: {rich_escape(plan.source.name)}"
            progress.update(task, description=description)
            progress.advance(task, 1)

        return execute_plans(plans, apply=True, copy_mode=copy_mode, on_conflict=on_conflict, on_progress=_on_progress)


def _dir_empty_after_removals(path: Path, removed_files: set[Path], removed_dirs: set[Path]) -> bool:
    try:
        entries = list(path.iterdir())
    except OSError:
        return False
    for entry in entries:
        if entry.is_dir():
            if entry in removed_dirs:
                continue
            return False
        if entry.is_file():
            if entry in removed_files:
                continue
            return False
    return True


def _prune_empty_dirs(
    plans: list[MovePlan],
    incoming_root: Path,
    *,
    dry_run: bool,
) -> None:
    removed_files = {plan.source for plan in plans}
    removed_dirs: set[Path] = set()
    for plan in plans:
        current = plan.source.parent
        while current != incoming_root:
            if current in removed_dirs:
                current = current.parent
                continue
            if _dir_empty_after_removals(current, removed_files, removed_dirs):
                if dry_run:
                    console.print(f"Would remove empty folder: {format_path(current)}")
                else:
                    try:
                        current.rmdir()
                    except OSError:
                        break
                removed_dirs.add(current)
                current = current.parent
                continue
            break


def _preview_group_key(plan: MovePlan) -> str:
    if plan.media_type == "tv":
        show = str(plan.metadata.get("show") or "").strip().casefold()
        if show:
            return f"tv:{show}"
    if plan.media_type == "movie":
        title = str(plan.metadata.get("title") or "").strip().casefold()
        if title:
            return f"movie:{title}"
    return f"path:{str(plan.destination.parent).casefold()}"


def _select_preview_plans(plans: list[MovePlan], limit: int = 5) -> list[MovePlan]:
    if len(plans) <= limit:
        return plans
    selected: list[MovePlan] = []
    seen_groups: set[str] = set()
    for plan in plans:
        group = _preview_group_key(plan)
        if group in seen_groups:
            continue
        selected.append(plan)
        seen_groups.add(group)
        if len(selected) >= limit:
            return selected
    if len(seen_groups) <= 1:
        return plans[:limit]
    for plan in plans:
        if plan in selected:
            continue
        selected.append(plan)
        if len(selected) >= limit:
            break
    return selected


def _preview_spans_multiple_groups(plans: list[MovePlan]) -> bool:
    groups = {_preview_group_key(plan) for plan in plans}
    return len(groups) > 1


def _print_run_summary(
    *,
    stats: PlanStats,
    plans: list[MovePlan],
    errors: list[str],
    result: ExecutionResult,
    cache_path: Path | None,
    report_path: Path | None,
    apply_report_path: Path | None = None,
) -> None:
    failures = stats.errors + len(errors) + len(result.errors)
    console.print("Summary:")
    console.print(f"Planned: {len(plans)}")
    console.print(f"Skipped: {stats.skipped}")
    console.print(f"Cache hits: {stats.cache_hits}")
    console.print(f"Manual entries: {stats.manual}")
    console.print(f"Failures: {failures}")
    console.print(f"Elapsed: {stats.elapsed:.2f}s")
    if cache_path is not None:
        console.print(f"Cache path: {format_path(cache_path)}")
    else:
        console.print("Cache path: disabled")
    if report_path is not None:
        console.print(f"Report path: {format_path(report_path)}")
    if apply_report_path is not None:
        console.print(f"Apply report path: {format_path(apply_report_path)}")


def _plan_items(
    incoming: Path,
    library: Path,
    mode: str,
    copy_mode: bool,
    interactive: bool,
    auto_accept: bool,
    min_confidence: float,
    extensions: str,
    cache_path: Path,
    limit: int | None,
    show_cache: bool,
    media_type_filter: str | None,
    use_cache: bool,
    on_conflict: str,
    offline: bool = False,
) -> tuple[list[MovePlan], list[str], PlanStats]:
    cache_store: Cache = Cache(cache_path) if use_cache else NullCache()
    exts = _parse_extensions(extensions)
    files = iter_video_files(incoming, exts)
    if limit:
        files = files[:limit]

    plans: list[MovePlan] = []
    errors: list[str] = []
    stats = PlanStats()
    started = time.monotonic()
    planned: dict[str, int] = {}
    collisions = 0
    history: list[HistoryEntry] = []
    episode_cache = EpisodeCache()
    media_type_overrides: dict[str, str] = {}
    tv_search_cache: dict[str, list[tvmaze.TVMazeShow]] = {}

    with Progress(
        TextColumn("{task.completed}/{task.total} - {task.description}"),
        disable=interactive or not sys.stdout.isatty(),
    ) as progress:
        task = progress.add_task("Planning files...", total=len(files))
        with tvmaze.create_session() as session_tv, wikidata.create_session() as session_wd:
            total = len(files)
            index = 0
            while index < len(files):
                path = files[index]
                progress.update(task, completed=min(index + 1, total), description=f"Planning: {rich_escape(path.name)}")
                try:
                    item = infer_item(path)
                    item, override_key = _resolve_media_type_override(item, cache_store, incoming, media_type_overrides)
                    log_event(
                        logger,
                        "file_inferred",
                        level=10,
                        path=path,
                        media_type=item.media_type,
                        title=item.title,
                        year=item.year,
                        season=item.season,
                        episode=item.episode,
                    )
                    if media_type_filter and item.media_type != media_type_filter:
                        index += 1
                        continue
                    _safe_print("", progress)
                    _console_for(progress).rule()
                    _safe_print(_file_panel(index + 1, total, item, incoming), progress)
                    cache_key = build_cache_key(item.path, incoming, item.media_type, item.year)
                    cache_snapshots: list[CacheSnapshot] = []
                    if override_key:
                        cache_snapshots.append(CacheSnapshot("show", override_key, cache_store.get_show(override_key)))
                    if item.media_type == "tv":
                        reusable_show_key = tv_show_cache_key(item.title, item.year) if _reusable_tv_cache_safe(item) else None
                        folder_show_key = tv_show_folder_cache_key(item.path, incoming)
                        keys = [cache_key]
                        if reusable_show_key:
                            keys.append(reusable_show_key)
                        if folder_show_key:
                            keys.append(folder_show_key)
                        if item.season is not None and item.episode is not None:
                            keys.append(tv_episode_cache_key(item.title, item.year, item.season, item.episode))
                        for key in keys:
                            cache_snapshots.append(CacheSnapshot("show", key, cache_store.get_show(key)))
                    else:
                        reusable_movie_key = movie_cache_key(item.title, item.year)
                        for key in [cache_key, reusable_movie_key]:
                            cache_snapshots.append(CacheSnapshot("movie", key, cache_store.get_movie(key)))
                    stats_snapshot = _snapshot_stats(stats)
                    errors_len = len(errors)
                    plan, collision = _process_item(
                        item=item,
                        library=library,
                        cache=cache_store,
                        mode=mode,
                        copy_mode=copy_mode,
                        interactive=interactive,
                        auto_accept=auto_accept,
                        min_confidence=min_confidence,
                        session_tv=session_tv,
                        session_wd=session_wd,
                        episode_cache=episode_cache,
                        progress=progress,
                        show_cache=show_cache,
                        stats=stats,
                        incoming_root=incoming,
                        planned=planned,
                        on_conflict=on_conflict,
                        allow_back=bool(history),
                        offline=offline,
                        media_type_overrides=media_type_overrides,
                        tv_search_cache=tv_search_cache,
                    )
                    history.append(
                        HistoryEntry(
                            index=index,
                            plan=plan,
                            collision=collision,
                            cache_snapshots=cache_snapshots,
                            stats_snapshot=stats_snapshot,
                            errors_len=errors_len,
                        )
                    )
                    if plan:
                        plans.append(plan)
                        if collision:
                            collisions += 1
                    index += 1
                except BackRequested:
                    if not history:
                        _safe_print("No previous decision to return to.", progress)
                        continue
                    entry = history.pop()
                    if entry.plan:
                        if plans and plans[-1] == entry.plan:
                            plans.pop()
                        else:
                            try:
                                plans.remove(entry.plan)
                            except ValueError:
                                pass
                        key = str(entry.plan.destination).lower()
                        if key in planned:
                            if planned[key] <= 1:
                                planned.pop(key, None)
                            else:
                                planned[key] -= 1
                    if entry.collision and collisions > 0:
                        collisions -= 1
                    for snapshot in entry.cache_snapshots:
                        if snapshot.section == "show":
                            if snapshot.previous is None:
                                cache_store.delete_show(snapshot.key)
                            else:
                                cache_store.set_show(snapshot.key, snapshot.previous)
                        else:
                            if snapshot.previous is None:
                                cache_store.delete_movie(snapshot.key)
                            else:
                                cache_store.set_movie(snapshot.key, snapshot.previous)
                    cache_store.save()
                    stats.auto_matched = entry.stats_snapshot.auto_matched
                    stats.user_confirmed = entry.stats_snapshot.user_confirmed
                    stats.manual = entry.stats_snapshot.manual
                    stats.skipped = entry.stats_snapshot.skipped
                    stats.errors = entry.stats_snapshot.errors
                    stats.cache_hits = entry.stats_snapshot.cache_hits
                    stats.elapsed = entry.stats_snapshot.elapsed
                    del errors[entry.errors_len:]
                    index = entry.index
                    back_path = files[index]
                    progress.update(
                        task,
                        completed=min(index + 1, total),
                        description=f"Planning: {rich_escape(back_path.name)}",
                    )
                    _safe_print("Rewound to previous file.", progress)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("planning_failed", extra={"path": path})
                    stats.errors += 1
                    errors.append(f"{path}: {exc}")
                    index += 1

    stats.elapsed = time.monotonic() - started
    if collisions:
        _safe_print(f"{collisions} collision(s) resolved by suffixing (2), (3), ...", None)
    return plans, errors, stats


def _build_command(config: BuildCommandConfig) -> str:
    parts = [
        "python -m plexify.cli organise",
        f"--incoming {shlex.quote(str(config.incoming))}",
        f"--library {shlex.quote(str(config.library))}",
    ]
    if config.mode != "dry-run":
        parts.append(f"--mode {config.mode}")
    if config.mode == "apply" and not config.copy_mode:
        parts.append("--move")
    if config.print_tree:
        parts.append("--print-tree")
    if config.extensions != DEFAULT_EXTENSIONS_LIST:
        extensions = ",".join(config.extensions)
        parts.append(f"--extensions {shlex.quote(extensions)}")
    if config.min_confidence != DEFAULT_MIN_CONFIDENCE:
        parts.append(f"--min-confidence {config.min_confidence}")
    if config.limit is not None:
        parts.append(f"--limit {config.limit}")
    if config.media_type != "auto":
        parts.append(f"--media-type {config.media_type}")
    if config.yes:
        parts.append("--yes")
    if config.no_cache:
        parts.append("--no-cache")
    if config.cache_file is not None:
        parts.append(f"--cache {shlex.quote(str(config.cache_file))}")
    if config.report is not None:
        parts.append(f"--report {shlex.quote(str(config.report))}")
    if config.clear_cache:
        parts.append("--clear-cache")
    if config.on_conflict != "rename":
        parts.append(f"--on-conflict {config.on_conflict}")
    if config.prune_empty_dirs:
        parts.append("--prune-empty-dirs")
    if not config.interactive:
        parts.append("--no-interactive")
    return " ".join(parts)


def _process_item(
    item: InferredItem,
    library: Path,
    cache: Cache,
    mode: str,
    copy_mode: bool,
    interactive: bool,
    auto_accept: bool,
    min_confidence: float,
    session_tv: requests.Session,
    session_wd: requests.Session,
    episode_cache: EpisodeCache,
    progress: Progress | None,
    show_cache: bool,
    stats: PlanStats | None = None,
    incoming_root: Path | None = None,
    planned: dict[str, int] | None = None,
    on_conflict: str = "rename",
    allow_back: bool = False,
    offline: bool = False,
    media_type_overrides: dict[str, str] | None = None,
    tv_search_cache: dict[str, list[tvmaze.TVMazeShow]] | None = None,
) -> tuple[MovePlan | None, bool]:
    item, override_key = _resolve_media_type_override(item, cache, incoming_root, media_type_overrides)
    folder_show_key = tv_show_folder_cache_key(item.path, incoming_root) if item.media_type == "tv" else None
    item = _apply_tv_folder_season_lock(item, cache, folder_show_key)
    cache_key = build_cache_key(item.path, incoming_root, item.media_type, item.year)
    if item.media_type == "movie" and interactive:
        if re.search(r"\b(series|episode)\b", item.path.stem, re.IGNORECASE):
            if _confirm("This looks like TV. Treat as TV? [Y/n]", True, progress, show_default=False):
                item = _switch_item_media_type(item, "tv")
                _persist_media_type_override(cache, override_key, "tv", media_type_overrides)
    reusable_movie_key = None
    reusable_show_key = None
    reusable_episode_key = None
    if item.media_type == "tv":
        if _reusable_tv_cache_safe(item):
            reusable_show_key = tv_show_cache_key(item.title, item.year)
        if item.season is not None and item.episode is not None:
            reusable_episode_key = tv_episode_cache_key(item.title, item.year, item.season, item.episode)
    else:
        reusable_movie_key = movie_cache_key(item.title, item.year)
    collision = False
    if item.media_type == "tv":
        raw_results_tv: list[tvmaze.TVMazeShow] | None = None
        next_offset = 0
        search_query = _build_search_query(item.title, None)
        page = _fetch_with_retry(
            "TVMaze",
            lambda: _tv_candidates(
                item,
                session_tv,
                cache,
                show_cache,
                incoming_root=incoming_root,
                cache_key=cache_key,
                offset=next_offset,
                raw_results=raw_results_tv,
                search_query=search_query,
                progress=progress,
                offline=offline,
                interactive=interactive,
                search_cache=tv_search_cache,
            ),
            interactive,
            progress,
        )
        if page is None:
            return None, False
        if page.cache_hit:
            _record_cache_hit(stats)
        candidates = page.candidates
        raw_results_tv = page.raw_results
        next_offset = page.next_offset
        has_more = page.has_more
        selected = None
        outcome = None
        while True:
            if not candidates:
                if not interactive:
                    if offline:
                        log_event(
                            logger,
                            "offline_no_cached_match",
                            media_type=item.media_type,
                            path=item.path,
                            title=item.title,
                        )
                    _record_stat(stats, "skipped")
                    return None, False
                if _confirm("No TV candidates. Switch to movie search? [y/N]", False, progress, show_default=False):
                    _persist_media_type_override(cache, override_key, "movie", media_type_overrides)
                    return _process_item(
                        item=_switch_item_media_type(item, "movie"),
                        library=library,
                        cache=cache,
                        mode=mode,
                        copy_mode=copy_mode,
                        interactive=interactive,
                        auto_accept=auto_accept,
                        min_confidence=min_confidence,
                        session_tv=session_tv,
                        session_wd=session_wd,
                        episode_cache=episode_cache,
                        progress=progress,
                        show_cache=show_cache,
                        stats=stats,
                        incoming_root=incoming_root,
                        planned=planned,
                        on_conflict=on_conflict,
                        allow_back=allow_back,
                        offline=offline,
                        media_type_overrides=media_type_overrides,
                        tv_search_cache=tv_search_cache,
                    )
                _safe_print(f"No candidates found for {rich_escape(item.title)}.", progress)
                empty_choice = _select_candidate(
                    "tv",
                    candidates,
                    progress,
                    has_more,
                    allow_search=True,
                    allow_manual=True,
                    allow_back=allow_back,
                    item=item,
                )
                if empty_choice == "s":
                    item, search_query = _prompt_search(item, progress)
                    raw_results_tv = None
                    next_offset = 0
                    page = _fetch_with_retry(
                        "TVMaze",
                        lambda: _tv_candidates(
                            item,
                            session_tv,
                            cache,
                            show_cache,
                            incoming_root=incoming_root,
                            cache_key=cache_key,
                            offset=next_offset,
                            raw_results=raw_results_tv,
                            search_query=search_query,
                            progress=progress,
                            offline=offline,
                            interactive=interactive,
                            search_cache=tv_search_cache,
                        ),
                        interactive,
                        progress,
                    )
                    if page is None:
                        return None, False
                    if page.cache_hit:
                        _record_cache_hit(stats)
                    candidates = page.candidates
                    raw_results_tv = page.raw_results
                    next_offset = page.next_offset
                    has_more = page.has_more
                    continue
                if isinstance(empty_choice, str) and empty_choice.startswith("search:"):
                    query = empty_choice.split("search:", 1)[1].strip()
                    if query:
                        item = _with_title(item, query)
                        search_query = _build_search_query(query, None)
                        raw_results_tv = None
                        next_offset = 0
                        page = _fetch_with_retry(
                            "TVMaze",
                            lambda: _tv_candidates(
                                item,
                                session_tv,
                                cache,
                                show_cache,
                                incoming_root=incoming_root,
                                cache_key=cache_key,
                                offset=next_offset,
                                raw_results=raw_results_tv,
                                search_query=search_query,
                                progress=progress,
                                offline=offline,
                                interactive=interactive,
                                search_cache=tv_search_cache,
                            ),
                            interactive,
                            progress,
                        )
                        if page is None:
                            return None, False
                        candidates = page.candidates
                        raw_results_tv = page.raw_results
                        next_offset = page.next_offset
                        has_more = page.has_more
                        continue
                if empty_choice == "m":
                    selected = _prompt_manual_tv(item, progress)
                    outcome = "manual"
                    break
                if empty_choice == "k":
                    _record_stat(stats, "skipped")
                    return None, False
                if empty_choice == "q":
                    raise typer.Exit(code=0)
                if empty_choice == "b":
                    raise BackRequested
                continue
            _maybe_fetch_episode_title(item, candidates[0], session_tv, episode_cache, bump_confidence=True)
            if auto_accept and _auto_acceptable(
                candidates,
                min_confidence,
                title=item.title,
                search_query=search_query,
                target_year=item.year,
            ):
                year_text = str(candidates[0].year) if candidates[0].year else "Unknown"
                _safe_print(f"Auto-selected: {candidates[0].title} ({year_text}) [{candidates[0].confidence:.2f}]", progress)
                selected = candidates[0]
                outcome = "auto"
                break
            if not interactive:
                _record_stat(stats, "skipped")
                return None, False
            if candidates[0].confidence < min_confidence:
                _safe_print(
                    f"Low confidence ({candidates[0].confidence:.2f} < {min_confidence:.2f}). "
                    "Press Enter to accept anyway, or choose s/m/k/q.",
                    progress,
                )
            _maybe_enrich_candidates("tv", candidates, session_tv, session_wd, cache, interactive)
            choice = _select_candidate(
                "tv",
                candidates,
                progress,
                has_more,
                allow_search=True,
                allow_manual=True,
                allow_back=allow_back,
                item=item,
            )
            if isinstance(choice, Candidate):
                selected = choice
                outcome = "confirmed"
                break
            if choice == "s":
                item, search_query = _prompt_search(item, progress)
                raw_results_tv = None
                next_offset = 0
                page = _fetch_with_retry(
                    "TVMaze",
                    lambda: _tv_candidates(
                        item,
                        session_tv,
                        cache,
                        show_cache,
                        incoming_root=incoming_root,
                        cache_key=cache_key,
                        offset=next_offset,
                        raw_results=raw_results_tv,
                        search_query=search_query,
                        progress=progress,
                        offline=offline,
                        interactive=interactive,
                        search_cache=tv_search_cache,
                    ),
                    interactive,
                    progress,
                )
                if page is None:
                    return None, False
                candidates = page.candidates
                raw_results_tv = page.raw_results
                next_offset = page.next_offset
                has_more = page.has_more
                continue
            if isinstance(choice, str) and choice.startswith("search:"):
                query = choice.split("search:", 1)[1].strip()
                if query:
                    item = _with_title(item, query)
                    search_query = _build_search_query(query, None)
                    raw_results_tv = None
                    next_offset = 0
                    page = _fetch_with_retry(
                        "TVMaze",
                        lambda: _tv_candidates(
                            item,
                            session_tv,
                            cache,
                            show_cache,
                            incoming_root=incoming_root,
                            cache_key=cache_key,
                            offset=next_offset,
                            raw_results=raw_results_tv,
                            search_query=search_query,
                            progress=progress,
                            offline=offline,
                            interactive=interactive,
                            search_cache=tv_search_cache,
                        ),
                        interactive,
                        progress,
                    )
                    if page is None:
                        return None, False
                    if page.cache_hit:
                        _record_cache_hit(stats)
                    candidates = page.candidates
                    raw_results_tv = page.raw_results
                    next_offset = page.next_offset
                    has_more = page.has_more
                    continue
            if choice == "n":
                page = _fetch_with_retry(
                    "TVMaze",
                    lambda: _tv_candidates(
                        item,
                        session_tv,
                        cache,
                        show_cache,
                        incoming_root=incoming_root,
                        cache_key=cache_key,
                        offset=next_offset,
                        raw_results=raw_results_tv,
                        search_query=search_query,
                        progress=progress,
                        offline=offline,
                        interactive=interactive,
                        search_cache=tv_search_cache,
                    ),
                    interactive,
                    progress,
                )
                if page is None:
                    return None, False
                if page.cache_hit:
                    _record_cache_hit(stats)
                candidates = page.candidates
                raw_results_tv = page.raw_results
                next_offset = page.next_offset
                has_more = page.has_more
                continue
            if choice == "m":
                selected = _prompt_manual_tv(item, progress)
                outcome = "manual"
                break
            if choice == "k":
                _record_stat(stats, "skipped")
                return None, False
            if choice == "q":
                raise typer.Exit(code=0)
            if choice == "b":
                raise BackRequested
        if not selected:
            _record_stat(stats, "skipped")
            return None, False
        if selected.metadata.get("manual"):
            outcome = "manual"
        if outcome is None:
            outcome = "confirmed"
        _record_stat(stats, outcome)
        _print_choice(selected, progress)
        _maybe_fetch_episode_title(item, selected, session_tv, episode_cache, bump_confidence=False)
        metadata = selected.metadata
        confirmed_by_user = outcome in {"confirmed", "manual"}
        trusted_auto = outcome == "auto" and not bool(selected.metadata.get("manual"))
        season = metadata.get("season") or item.season
        episode = metadata.get("episode") or item.episode
        episode_title = metadata.get("episode_title") or item.episode_title
        if interactive and (season is None or episode is None) and item.episode_title:
            resolved = _resolve_episode_from_title(item, metadata.get("id"), session_tv, episode_cache, progress)
            if resolved:
                season, episode, resolved_title = resolved
                episode_title = resolved_title or episode_title
                metadata["episode_title"] = episode_title
        if season is None or episode is None:
            if not interactive:
                return None, False
            season_prompt = _prompt_int_or_control("Season", item.season or 1, progress)
            if season_prompt == "k":
                _record_stat(stats, "skipped")
                return None, False
            if season_prompt == "q":
                raise typer.Exit(code=0)
            season = season_prompt
            episode_prompt = _prompt_int_or_control("Episode", item.episode or 1, progress)
            if episode_prompt == "k":
                _record_stat(stats, "skipped")
                return None, False
            if episode_prompt == "q":
                raise typer.Exit(code=0)
            episode = episode_prompt
            if not episode_title:
                episode_title = _prompt_text("Episode title (optional)", item.episode_title or "", progress)

        metadata["episode_title"] = episode_title
        if selected.metadata.get("manual"):
            entry = {
                "id": None,
                "name": metadata["name"],
                "premiered": None,
                "chosen_title": metadata["name"],
                "chosen_year": metadata.get("year"),
                "season": season,
                "episode": episode,
                "episode_title": episode_title,
                "manual": True,
                "confirmed_by_user": confirmed_by_user,
                "selection_mode": outcome,
                "created_at": now_timestamp(),
                "source": "Manual",
            }
            show_entry = {
                "id": None,
                "name": metadata["name"],
                "premiered": None,
                "chosen_title": metadata["name"],
                "chosen_year": metadata.get("year"),
                "season": season,
                "manual": True,
                "confirmed_by_user": confirmed_by_user,
                "selection_mode": outcome,
                "created_at": now_timestamp(),
                "source": "Manual",
            }
        else:
            entry = {
                "id": metadata["id"],
                "name": selected.title,
                "premiered": selected.year,
                "chosen_title": selected.title,
                "chosen_year": selected.year,
                "season": season,
                "episode": episode,
                "episode_title": episode_title,
                "manual": False,
                "confirmed_by_user": confirmed_by_user,
                "selection_mode": outcome,
                "created_at": now_timestamp(),
                "source": selected.source,
            }
            show_entry = {
                "id": metadata["id"],
                "name": selected.title,
                "premiered": selected.year,
                "chosen_title": selected.title,
                "chosen_year": selected.year,
                "manual": False,
                "confirmed_by_user": confirmed_by_user,
                "selection_mode": outcome,
                "created_at": now_timestamp(),
                "source": selected.source,
            }
        cache.set_show(cache_key, entry)
        if reusable_show_key:
            cache.set_show(reusable_show_key, show_entry)
        if folder_show_key and (confirmed_by_user or trusted_auto):
            cache.set_show(folder_show_key, show_entry)
        if reusable_episode_key:
            cache.set_show(reusable_episode_key, entry)
        cache.save()
        destination = plan_tv_show(
            library,
            metadata.get("name") or selected.title,
            metadata.get("year") or selected.year,
            int(season),
            int(episode),
            metadata.get("episode_title") or episode_title,
            item.path.suffix,
        )
        destination, collision = _resolve_destination(destination, on_conflict, planned, progress)
        if destination is None:
            _record_stat(stats, "skipped")
            return None, False
        if len(str(destination)) > 240:
            _safe_print("Warning: destination path is very long and may exceed Windows limits.", progress)
        plan = MovePlan(
            source=item.path,
            destination=destination,
            mode=mode,
            media_type="tv",
            metadata={
                "show": metadata.get("name") or selected.title,
                "year": metadata.get("year") or selected.year,
                "season": int(season),
                "episode": int(episode),
                "episode_title": metadata.get("episode_title") or episode_title,
            },
        )
        _print_plan(plan, progress)
        log_event(
            logger,
            "plan_created",
            source_path=item.path,
            destination=destination,
            media_type="tv",
            title=metadata.get("name") or selected.title,
            year=metadata.get("year") or selected.year,
            season=int(season),
            episode=int(episode),
        )
        return plan, collision

    raw_results_movie: list[wikidata.WikidataCandidate] | None = None
    next_offset = 0
    search_query = _build_search_query(item.title, None)
    page = _fetch_with_retry(
        "Wikidata",
        lambda: _movie_candidates(
            item,
            session_wd,
            cache,
            show_cache,
            cache_key=cache_key,
            offset=next_offset,
            raw_results=raw_results_movie,
            search_query=search_query,
            progress=progress,
            offline=offline,
            interactive=interactive,
        ),
        interactive,
        progress,
    )
    if page is None:
        return None, False
    if page.cache_hit:
        _record_cache_hit(stats)
    candidates = page.candidates
    raw_results_movie = page.raw_results
    next_offset = page.next_offset
    has_more = page.has_more
    selected = None
    manual_fallback: Candidate | None = None
    manual_hint = ""
    outcome = None
    while True:
        if not candidates:
            if not interactive:
                if offline:
                    log_event(
                        logger,
                        "offline_no_cached_match",
                        media_type=item.media_type,
                        path=item.path,
                        title=item.title,
                    )
                _record_stat(stats, "skipped")
                return None, False
            if _confirm("No movie candidates. Switch to TV search? [y/N]", False, progress, show_default=False):
                _persist_media_type_override(cache, override_key, "tv", media_type_overrides)
                return _process_item(
                    item=_switch_item_media_type(item, "tv"),
                    library=library,
                    cache=cache,
                    mode=mode,
                    copy_mode=copy_mode,
                    interactive=interactive,
                    auto_accept=auto_accept,
                    min_confidence=min_confidence,
                    session_tv=session_tv,
                    session_wd=session_wd,
                    episode_cache=episode_cache,
                    progress=progress,
                    show_cache=show_cache,
                    stats=stats,
                    incoming_root=incoming_root,
                    planned=planned,
                    on_conflict=on_conflict,
                    allow_back=allow_back,
                    offline=offline,
                    media_type_overrides=media_type_overrides,
                    tv_search_cache=tv_search_cache,
                )
            _safe_print(f"No candidates found for {rich_escape(item.title)}.", progress)
            empty_choice = _select_candidate(
                "movie",
                candidates,
                progress,
                has_more,
                allow_search=True,
                allow_manual=True,
                allow_back=allow_back,
                item=item,
            )
            if empty_choice == "s":
                item, search_query = _prompt_search(item, progress)
                raw_results_movie = None
                next_offset = 0
                page = _fetch_with_retry(
                    "Wikidata",
                    lambda: _movie_candidates(
                        item,
                        session_wd,
                        cache,
                        show_cache,
                        cache_key=cache_key,
                        offset=next_offset,
                        raw_results=raw_results_movie,
                        search_query=search_query,
                        progress=progress,
                        offline=offline,
                        interactive=interactive,
                    ),
                    interactive,
                    progress,
                )
                if page is None:
                    return None, False
                if page.cache_hit:
                    _record_cache_hit(stats)
                candidates = page.candidates
                raw_results_movie = page.raw_results
                next_offset = page.next_offset
                has_more = page.has_more
                continue
            if isinstance(empty_choice, str) and empty_choice.startswith("search:"):
                query = empty_choice.split("search:", 1)[1].strip()
                if query:
                    item = _with_title(item, query)
                    search_query = _build_search_query(query, None)
                    raw_results_movie = None
                    next_offset = 0
                    page = _fetch_with_retry(
                        "Wikidata",
                        lambda: _movie_candidates(
                            item,
                            session_wd,
                            cache,
                            show_cache,
                            cache_key=cache_key,
                            offset=next_offset,
                            raw_results=raw_results_movie,
                            search_query=search_query,
                            progress=progress,
                            offline=offline,
                            interactive=interactive,
                        ),
                        interactive,
                        progress,
                    )
                    if page is None:
                        return None, False
                    if page.cache_hit:
                        _record_cache_hit(stats)
                    candidates = page.candidates
                    raw_results_movie = page.raw_results
                    next_offset = page.next_offset
                    has_more = page.has_more
                    continue
            if empty_choice == "m":
                if manual_fallback is None:
                    manual_fallback, manual_hint = _prompt_manual_movie(item, progress)
                if manual_fallback.year is None and interactive:
                    item = _with_title(item, manual_fallback.title)
                    search_query = _build_search_query(manual_fallback.title, manual_hint)
                    raw_results_movie = None
                    next_offset = 0
                    page = _fetch_with_retry(
                        "Wikidata",
                        lambda: _movie_candidates(
                            item,
                            session_wd,
                            cache,
                            show_cache,
                            cache_key=cache_key,
                            offset=next_offset,
                            raw_results=raw_results_movie,
                            search_query=search_query,
                            progress=progress,
                            offline=offline,
                            interactive=interactive,
                        ),
                        interactive,
                        progress,
                    )
                    if page is None:
                        selected = manual_fallback
                        outcome = "manual"
                        break
                    candidates = page.candidates
                    raw_results_movie = page.raw_results
                    next_offset = page.next_offset
                    has_more = page.has_more
                    continue
                selected = manual_fallback
                outcome = "manual"
                break
            if empty_choice == "k":
                _record_stat(stats, "skipped")
                return None, False
            if empty_choice == "q":
                raise typer.Exit(code=0)
            if empty_choice == "b":
                raise BackRequested
            continue
        if auto_accept and _auto_acceptable(
            candidates,
            min_confidence,
            title=item.title,
            search_query=search_query,
            target_year=item.year,
        ):
            year_text = str(candidates[0].year) if candidates[0].year else "Unknown"
            _safe_print(f"Auto-selected: {candidates[0].title} ({year_text}) [{candidates[0].confidence:.2f}]", progress)
            selected = candidates[0]
            outcome = "auto"
            break
        if not interactive:
            _record_stat(stats, "skipped")
            return None, False
        if candidates[0].confidence < min_confidence:
            _safe_print(
                f"Low confidence ({candidates[0].confidence:.2f} < {min_confidence:.2f}). "
                "Press Enter to accept anyway, or choose s/m/k/q.",
                progress,
            )
        _maybe_enrich_candidates("movie", candidates, session_tv, session_wd, cache, interactive)
        choice = _select_candidate(
            "movie",
            candidates,
            progress,
            has_more,
            allow_search=True,
            allow_manual=True,
            allow_back=allow_back,
            item=item,
        )
        if isinstance(choice, Candidate):
            selected = choice
            outcome = "confirmed"
            break
        if choice == "s":
            item, search_query = _prompt_search(item, progress)
            raw_results_movie = None
            next_offset = 0
            page = _fetch_with_retry(
                "Wikidata",
                lambda: _movie_candidates(
                    item,
                    session_wd,
                    cache,
                    show_cache,
                    cache_key=cache_key,
                    offset=next_offset,
                    raw_results=raw_results_movie,
                    search_query=search_query,
                    progress=progress,
                    offline=offline,
                    interactive=interactive,
                ),
                interactive,
                progress,
            )
            if page is None:
                return None, False
            if page.cache_hit:
                _record_cache_hit(stats)
            candidates = page.candidates
            raw_results_movie = page.raw_results
            next_offset = page.next_offset
            has_more = page.has_more
            continue
        if isinstance(choice, str) and choice.startswith("search:"):
            query = choice.split("search:", 1)[1].strip()
            if query:
                item = _with_title(item, query)
                search_query = _build_search_query(query, None)
                raw_results_movie = None
                next_offset = 0
                page = _fetch_with_retry(
                    "Wikidata",
                    lambda: _movie_candidates(
                        item,
                        session_wd,
                        cache,
                        show_cache,
                        cache_key=cache_key,
                        offset=next_offset,
                        raw_results=raw_results_movie,
                        search_query=search_query,
                        progress=progress,
                        offline=offline,
                        interactive=interactive,
                    ),
                    interactive,
                    progress,
                )
                if page is None:
                    return None, False
                candidates = page.candidates
                raw_results_movie = page.raw_results
                next_offset = page.next_offset
                has_more = page.has_more
                continue
        if choice == "n":
            page = _fetch_with_retry(
                "Wikidata",
                lambda: _movie_candidates(
                    item,
                    session_wd,
                    cache,
                    show_cache,
                    cache_key=cache_key,
                    offset=next_offset,
                    raw_results=raw_results_movie,
                    search_query=search_query,
                    progress=progress,
                    offline=offline,
                    interactive=interactive,
                ),
                interactive,
                progress,
            )
            if page is None:
                return None, False
            candidates = page.candidates
            raw_results_movie = page.raw_results
            next_offset = page.next_offset
            has_more = page.has_more
            continue
        if choice == "m":
            if manual_fallback is None:
                manual_fallback, manual_hint = _prompt_manual_movie(item, progress)
            if manual_fallback.year is None and interactive:
                item = _with_title(item, manual_fallback.title)
                search_query = _build_search_query(manual_fallback.title, manual_hint)
                raw_results_movie = None
                next_offset = 0
                page = _fetch_with_retry(
                    "Wikidata",
                    lambda: _movie_candidates(
                        item,
                        session_wd,
                        cache,
                        show_cache,
                        cache_key=cache_key,
                        offset=next_offset,
                        raw_results=raw_results_movie,
                        search_query=search_query,
                        progress=progress,
                        offline=offline,
                        interactive=interactive,
                    ),
                    interactive,
                    progress,
                )
                if page is None:
                    selected = manual_fallback
                    outcome = "manual"
                    break
                candidates = page.candidates
                raw_results_movie = page.raw_results
                next_offset = page.next_offset
                has_more = page.has_more
                continue
            selected = manual_fallback
            outcome = "manual"
            break
        if choice == "k":
            _record_stat(stats, "skipped")
            return None, False
        if choice == "q":
            raise typer.Exit(code=0)
        if choice == "b":
            raise BackRequested
    if not selected:
        _record_stat(stats, "skipped")
        return None, False
    if selected.metadata.get("manual"):
        outcome = "manual"
    if outcome is None:
        outcome = "confirmed"
    _record_stat(stats, outcome)
    _print_choice(selected, progress)
    metadata = selected.metadata
    confirmed_by_user = outcome in {"confirmed", "manual"}
    if metadata.get("manual"):
        entry = {
            "qid": None,
            "title": metadata["title"],
            "year": metadata.get("year"),
            "chosen_title": metadata["title"],
            "chosen_year": metadata.get("year"),
            "manual": True,
            "confirmed_by_user": confirmed_by_user,
            "selection_mode": outcome,
            "created_at": now_timestamp(),
            "source": "Manual",
        }
    else:
        entry = {
            "qid": metadata["qid"],
            "title": selected.title,
            "year": selected.year,
            "chosen_title": selected.title,
            "chosen_year": selected.year,
            "manual": False,
            "confirmed_by_user": confirmed_by_user,
            "selection_mode": outcome,
            "created_at": now_timestamp(),
            "source": selected.source,
        }
    cache.set_movie(cache_key, entry)
    if reusable_movie_key and _reusable_movie_cache_safe(item):
        cache.set_movie(reusable_movie_key, entry)
    cache.save()

    year = metadata.get("year") or selected.year
    if year is None and interactive:
        year_text = _prompt_text("Movie year (optional, helps disambiguate)", "", progress, show_default=False)
        year = int(year_text) if year_text else None
    destination = plan_movie(library, metadata.get("title") or selected.title, year, item.path.suffix)
    destination, collision = _resolve_destination(destination, on_conflict, planned, progress)
    if destination is None:
        _record_stat(stats, "skipped")
        return None, False
    if len(str(destination)) > 240:
        _safe_print("Warning: destination path is very long and may exceed Windows limits.", progress)
    plan = MovePlan(
        source=item.path,
        destination=destination,
        mode=mode,
        media_type="movie",
        metadata={"title": metadata.get("title") or selected.title, "year": year},
    )
    _print_plan(plan, progress)
    log_event(
        logger,
        "plan_created",
        source_path=item.path,
        destination=destination,
        media_type="movie",
        title=metadata.get("title") or selected.title,
        year=year,
    )
    return plan, collision


@app.command()
def organise(
    incoming: Path = typer.Option(..., exists=True, file_okay=False, dir_okay=True, help="Folder to scan"),
    library: Path = typer.Option(..., file_okay=False, dir_okay=True, help="Library root"),
    mode: str = typer.Option("dry-run", help="dry-run or apply"),
    move: bool = typer.Option(False, "--move", help="Move files (overrides default copy)", is_flag=True),
    copy: bool = typer.Option(False, "--copy", help="Copy files (default behaviour for apply)", is_flag=True),
    extensions: str = typer.Option(DEFAULT_EXTENSIONS, help="Comma-separated extensions"),
    min_confidence: float = typer.Option(DEFAULT_MIN_CONFIDENCE, help="Minimum confidence for auto acceptance"),
    cache: Path = typer.Option(None, help="Cache path"),
    report: Path = typer.Option(None, help="Report path"),
    yes: bool = typer.Option(False, "--yes", help="Auto-accept top result when confidence >= 0.90", is_flag=True),
    limit: int = typer.Option(None, help="Limit number of files"),
    print_tree: bool = typer.Option(False, "--print-tree", help="Print planned destination tree", is_flag=True),
    interactive: bool = typer.Option(False, "--interactive", help="Force interactive mode", is_flag=True),
    no_interactive: bool = typer.Option(False, "--no-interactive", help="Disable interactive prompts", is_flag=True),
    media_type: str = typer.Option("auto", "--media-type", help="Filter by media type: auto/movie/tv"),
    no_cache: bool = typer.Option(False, "--no-cache", help="Disable cache reads/writes", is_flag=True),
    clear_cache: bool = typer.Option(False, "--clear-cache", help="Clear cache before running", is_flag=True),
    offline: bool = typer.Option(False, "--offline", help="Disable network lookups for this run", is_flag=True),
    on_conflict: str = typer.Option("rename", "--on-conflict", help="On destination conflict: rename/skip/overwrite"),
    log_level: str = typer.Option("WARNING", "--log-level", help="Log level: DEBUG/INFO/WARNING/ERROR"),
    log_format: str = typer.Option("text", "--log-format", help="Log format: text/json"),
    log_file: Path = typer.Option(None, "--log-file", help="Optional log file path"),
    prune_empty_dirs: bool = typer.Option(
        False, "--prune-empty-dirs", help="Remove empty folders after move", is_flag=True
    ),
) -> None:
    _initialise_logging(log_level, log_format, log_file)
    run_id = uuid.uuid4().hex
    log_event(
        logger,
        "run_started",
        run_id=run_id,
        command="organise",
        incoming=incoming,
        library=library,
        mode=mode,
    )

    if move and copy:
        console.print("Choose only one of --move or --copy.")
        raise typer.Exit(code=2)
    if interactive and no_interactive:
        console.print("Choose only one of --interactive or --no-interactive.")
        raise typer.Exit(code=2)
    if mode not in {"dry-run", "apply"}:
        console.print("Invalid mode. Use dry-run or apply.")
        raise typer.Exit(code=2)
    if media_type not in {"auto", "movie", "tv"}:
        console.print("Invalid media type. Use auto, movie, or tv.")
        raise typer.Exit(code=2)
    if on_conflict not in {"rename", "skip", "overwrite"}:
        console.print("Invalid on-conflict policy. Use rename, skip, or overwrite.")
        raise typer.Exit(code=2)

    try:
        ensure_non_overlapping_paths(incoming, library, label_source="Incoming", label_library="Library")
    except PathOverlapError as exc:
        _print_overlap_error(exc)
        raise typer.Exit(code=2)

    interactive_mode = True if interactive else not no_interactive
    if mode == "dry-run":
        console.print("DRY-RUN: no files will be moved/copied.")
    if offline:
        console.print("Offline mode enabled: network lookups disabled.")
        log_event(logger, "offline_mode_enabled", run_id=run_id, command="organise")
    if mode == "apply":
        if move:
            copy_mode = False
        elif copy:
            copy_mode = True
        else:
            copy_mode = True
    else:
        copy_mode = copy

    cache_path = cache or library / ".plexify" / "cache.json"
    report_path = report or library / ".plexify" / "reports" / f"{now_timestamp()}.json"
    if clear_cache:
        cache_path.unlink(missing_ok=True)

    media_type_filter = None if media_type == "auto" else media_type
    plans, errors, stats = _plan_items(
        incoming=incoming,
        library=library,
        mode=mode,
        copy_mode=copy_mode,
        interactive=interactive_mode,
        auto_accept=yes,
        min_confidence=min_confidence,
        extensions=extensions,
        cache_path=cache_path,
        limit=limit,
        show_cache=interactive_mode or print_tree,
        media_type_filter=media_type_filter,
        use_cache=not no_cache,
        on_conflict=on_conflict,
        offline=offline,
    )

    if print_tree and plans:
        tree = _build_tree([plan.destination for plan in plans])
        console.print(tree)

    apply_mode = mode == "apply"
    if apply_mode and interactive_mode:
        console.print("Plan summary:")
        console.print(f"Planned items: {len(plans)}")
        console.print(f"Skipped: {stats.skipped}")
        console.print(f"Errors: {stats.errors + len(errors)}")
        preview = _select_preview_plans(plans, limit=5)
        if preview:
            if _preview_spans_multiple_groups(preview):
                console.print("Preview (sampled across shows/titles):")
            else:
                console.print("Preview:")
            for plan in preview:
                console.print(f"FROM: {format_path(plan.source)}")
                console.print(f"TO:   {format_path(plan.destination)}")
        if not copy_mode:
            console.print("Warning: move will remove the original files from the incoming folder.")
            if not _confirm_move(None):
                console.print("Cancelled. No changes were made.")
                raise typer.Exit(code=0)
        else:
            if not _confirm("Apply this plan now? [y/N]", False, None, show_default=False):
                console.print("Cancelled. No changes were made.")
                raise typer.Exit(code=0)
    if apply_mode and plans:
        result = _apply_with_progress(plans, copy_mode=copy_mode, on_conflict=on_conflict)
    else:
        result = execute_plans(plans, apply=apply_mode, copy_mode=copy_mode, on_conflict=on_conflict)

    if prune_empty_dirs and not copy_mode and plans:
        if apply_mode:
            _prune_empty_dirs(result.moved, incoming, dry_run=False)
        else:
            _prune_empty_dirs(plans, incoming, dry_run=True)

    write_report(report_path, result.moved if apply_mode else plans, mode, copy_mode)
    _print_run_summary(
        stats=stats,
        plans=plans,
        errors=errors,
        result=result,
        cache_path=None if no_cache else cache_path,
        report_path=report_path,
    )

    apply_report_path = None
    if not apply_mode and interactive_mode and plans:
        if _confirm("Apply these changes now? [y/N]", False, None, show_default=False):
            if not copy_mode:
                console.print("Warning: move will remove the original files from the incoming folder.")
                if not _confirm_move(None):
                    console.print("Cancelled. No changes were made.")
                else:
                    result = _apply_with_progress(plans, copy_mode=copy_mode, on_conflict=on_conflict)
                    if prune_empty_dirs:
                        _prune_empty_dirs(result.moved, incoming, dry_run=False)
                    apply_report_path = library / ".plexify" / "reports" / f"{now_timestamp()}.json"
                    write_report(apply_report_path, result.moved, "apply", copy_mode)
            else:
                result = _apply_with_progress(plans, copy_mode=copy_mode, on_conflict=on_conflict)
                apply_report_path = library / ".plexify" / "reports" / f"{now_timestamp()}.json"
                write_report(apply_report_path, result.moved, "apply", copy_mode)

    if not apply_mode:
        apply_config = BuildCommandConfig(
            incoming=incoming,
            library=library,
            media_type=media_type,
            mode="apply",
            copy_mode=copy_mode,
            extensions=_parse_extensions(extensions),
            min_confidence=min_confidence,
            limit=limit,
            interactive=interactive_mode,
            print_tree=print_tree,
            show_enrichment=False,
            yes=yes,
            no_cache=no_cache,
            cache_file=cache,
            clear_cache=clear_cache,
            report=None,
            on_conflict=on_conflict,
            prune_empty_dirs=prune_empty_dirs,
        )
        console.print("Apply command:")
        console.print(_build_command(apply_config))
        if apply_report_path is not None:
            console.print(f"Apply report written: {format_path(apply_report_path)}")

    if result.errors or errors:
        log_event(
            logger,
            "run_finished",
            run_id=run_id,
            command="organise",
            status="error",
            planned_count=len(plans),
            error_count=len(result.errors) + len(errors),
        )
        console.print("Errors:")
        for error in result.errors + errors:
            console.print(f"- {rich_escape(error)}")
        raise typer.Exit(code=1)
    if not plans:
        log_event(
            logger,
            "run_finished",
            run_id=run_id,
            command="organise",
            status="empty",
            planned_count=0,
            error_count=0,
        )
        raise typer.Exit(code=1)
    log_event(
        logger,
        "run_finished",
        run_id=run_id,
        command="organise",
        status="success",
        planned_count=len(plans),
        error_count=0,
    )
    raise typer.Exit(code=0)


@app.command()
def music(
    source: Path = typer.Option(None, "--source", help="Folder containing album directories"),
    library: Path = typer.Option(None, "--library", help="Library root (will contain Music)"),
    apply: bool = typer.Option(False, "--apply/--dry-run", help="Apply changes or dry-run"),
    copy: bool = typer.Option(False, "--copy", help="Copy files instead of moving", is_flag=True),
    extensions: str = typer.Option(DEFAULT_MUSIC_EXTENSIONS, help="Comma-separated extensions"),
    verify: bool = typer.Option(True, "--verify/--no-verify", help="Verify albums via MusicBrainz"),
    keep_art: bool = typer.Option(True, "--keep-art/--no-art", help="Move/copy album artwork to cover.jpg"),
    keep_cue: bool = typer.Option(False, "--keep-cue", help="Keep .cue sidecars", is_flag=True),
    keep_log: bool = typer.Option(False, "--keep-log", help="Keep .log sidecars", is_flag=True),
    offline: bool = typer.Option(False, "--offline", help="Disable network lookups for this run", is_flag=True),
    cleanup_empty_dirs: bool = typer.Option(
        False, "--cleanup-empty-dirs", help="Remove empty folders after move", is_flag=True
    ),
    verbose_plan: bool = typer.Option(False, "--verbose-plan", help="Print per-track plan output", is_flag=True),
    log_level: str = typer.Option("WARNING", "--log-level", help="Log level: DEBUG/INFO/WARNING/ERROR"),
    log_format: str = typer.Option("text", "--log-format", help="Log format: text/json"),
    log_file: Path = typer.Option(None, "--log-file", help="Optional log file path"),
) -> None:
    _initialise_logging(log_level, log_format, log_file)
    run_id = uuid.uuid4().hex
    log_event(
        logger,
        "run_started",
        run_id=run_id,
        command="music",
        source=source,
        library=library,
        mode="apply" if apply else "dry-run",
    )
    if offline:
        console.print("Offline mode enabled: network lookups disabled.")
        log_event(logger, "offline_mode_enabled", run_id=run_id, command="music")

    if source is None:
        source_default, library_default = _wizard_defaults("music")
        while True:
            source_text = _prompt_path(
                "Source folder",
                str(source_default) if source_default is not None else None,
                directories_only=True,
            )
            while not source_text.strip():
                console.print("Please enter a folder path.")
                source_text = _prompt_path(
                    "Source folder",
                    str(source_default) if source_default is not None else None,
                    directories_only=True,
                )
            source = Path(source_text)
            if source.exists() and source.is_dir():
                break
            console.print("That path does not exist or is not a folder. Please try again.")
    if library is None:
        if source is not None and "library_default" not in locals():
            _source_default, library_default = _wizard_defaults("music")
        while True:
            library_text = _prompt_path(
                "Library folder",
                str(library_default) if library_default is not None else None,
                directories_only=True,
            )
            while not library_text.strip():
                console.print("Please enter a folder path.")
                library_text = _prompt_path(
                    "Library folder",
                    str(library_default) if library_default is not None else None,
                    directories_only=True,
                )
            library = Path(library_text)
            if library.exists() and library.is_file():
                console.print("That path is a file. Please choose a folder path.")
                continue
            if not library.exists():
                if _confirm("That folder does not exist. Create it? [Y/n]", True, None, show_default=False):
                    library.mkdir(parents=True, exist_ok=True)
                    break
                continue
            break

    if source is None or library is None:
        raise typer.Exit(code=2)
    if not source.exists() or not source.is_dir():
        console.print("Source folder must exist and be a directory.")
        raise typer.Exit(code=2)
    if library.exists() and library.is_file():
        console.print("Library path must be a directory.")
        raise typer.Exit(code=2)
    if not library.exists():
        if not sys.stdin.isatty():
            library.mkdir(parents=True, exist_ok=True)
            console.print(f"Library folder created: {format_path(library)}")
        elif _confirm("Library folder does not exist. Create it? [Y/n]", True, None, show_default=False):
            library.mkdir(parents=True, exist_ok=True)
        else:
            console.print("Cancelled. No changes were made.")
            raise typer.Exit(code=0)
    try:
        ensure_non_overlapping_paths(source, library, label_source="Source", label_library="Library")
    except PathOverlapError as exc:
        _print_overlap_error(exc)
        raise typer.Exit(code=2)
    _save_wizard_prefs("music", source, library)

    if not apply:
        console.print("DRY-RUN: no files will be moved/copied.")
    copy_mode = copy
    if offline and verify:
        console.print("Offline mode: MusicBrainz verification disabled for this run.")
        verify = False

    albums, errors = music_util.discover_albums(source, _parse_extensions(extensions))
    if not albums:
        console.print("No valid albums found.")
        for error in errors:
            console.print(f"- {rich_escape(error)}")
        raise typer.Exit(code=1)

    mb_disabled_reported = False
    if verify and not musicbrainz.is_available():
        reason = musicbrainz.unavailable_reason() or "offline"
        console.print(f"MusicBrainz disabled: {reason}")
        mb_disabled_reported = True

    planned: dict[str, int] = {}
    plans: list[MovePlan] = []
    for idx, album in enumerate(albums, start=1):
        console.print(_album_panel(idx, len(albums), album))
        album_artist = album.artist
        album_title = album.album
        planned_tracks = _music_tracks_from_filenames(album.tracks)

        if verify:
            if not musicbrainz.is_available():
                if not mb_disabled_reported:
                    reason = musicbrainz.unavailable_reason() or "offline"
                    console.print(f"MusicBrainz disabled: {reason}")
                    mb_disabled_reported = True
                console.print("Skipped MusicBrainz (offline).")
            else:
                candidates = musicbrainz.search_releases(album_artist, album_title, limit=8)
                if not musicbrainz.is_available():
                    if not mb_disabled_reported:
                        reason = musicbrainz.unavailable_reason() or "offline"
                        console.print(f"MusicBrainz disabled: {reason}")
                        mb_disabled_reported = True
                    console.print("Skipped MusicBrainz (offline).")
                elif not candidates:
                    console.print("No MusicBrainz matches found. Using filename metadata.")
                else:
                    candidates = _rank_music_candidates(candidates, len(album.tracks))
                    while True:
                        selection = _select_music_candidate(candidates)
                        if selection == "q":
                            raise typer.Exit(code=0)
                        if selection == "s":
                            console.print("Skipping MusicBrainz verification for this album.")
                            break
                        if not isinstance(selection, musicbrainz.ReleaseCandidate):
                            break
                        album_artist = selection.artist
                        album_title = selection.title
                        mb_tracks = musicbrainz.fetch_release_tracks(selection.mbid)
                        if not mb_tracks:
                            if not musicbrainz.is_available():
                                if not mb_disabled_reported:
                                    reason = musicbrainz.unavailable_reason() or "offline"
                                    console.print(f"MusicBrainz disabled: {reason}")
                                    mb_disabled_reported = True
                                console.print("Skipped MusicBrainz (offline).")
                            else:
                                console.print("No tracklist found. Using filename metadata.")
                            break

                        if len(mb_tracks) != len(album.tracks):
                            console.print(
                                f"Track count mismatch: files={len(album.tracks)} release={len(mb_tracks)}."
                            )
                            choice = _prompt_choice(
                                "r=re-pick release | f=filename titles | o=order",
                                "r",
                                None,
                                show_default=False,
                            )
                            if choice == "r":
                                continue
                            if choice == "o":
                                mapped = _map_musicbrainz_by_order(album.tracks, mb_tracks)
                                console.print("Using MusicBrainz titles by track order.")
                                planned_tracks = mapped
                                break
                            console.print("Using filename metadata.")
                            break

                        mapped, reason = _map_musicbrainz_tracks(album.tracks, mb_tracks)
                        if reason:
                            console.print(f"Warning: {reason}.")
                            if _confirm("Fallback to filename titles? [Y/n]", True, None, show_default=False):
                                mapped = None
                            else:
                                mapped = _map_musicbrainz_by_order(album.tracks, mb_tracks)
                                console.print("Using MusicBrainz titles by track order.")
                        if mapped is not None:
                            planned_tracks = mapped
                        break

        dest_artist = "Various Artists" if _should_use_various_artists(album, album_artist) else album_artist
        dest_album = album_title

        for track in planned_tracks:
            destination = music_util.track_destination(
                library,
                dest_artist,
                dest_album,
                track.track_number_text,
                track.track_title,
                track.ext,
            )
            destination, _collision = _resolve_destination(destination, "rename", planned, None)
            if destination is None:
                continue
            plan = MovePlan(
                source=track.source,
                destination=destination,
                mode="apply" if apply else "dry-run",
                media_type="music",
                metadata={
                    "artist": dest_artist,
                    "album": dest_album,
                    "track_number": track.track_number,
                },
            )
            plans.append(plan)
            if verbose_plan:
                _print_plan(plan, None)

        album_folder = music_util.album_destination(library, dest_artist, dest_album)
        if keep_art:
            artwork = music_util.select_best_artwork(album.images)
            if artwork:
                destination = album_folder / "cover.jpg"
                destination, _collision = _resolve_destination(destination, "rename", planned, None)
                if destination is not None:
                    plans.append(
                        MovePlan(
                            source=artwork,
                            destination=destination,
                            mode="apply" if apply else "dry-run",
                            media_type="music",
                            metadata={"artist": dest_artist, "album": dest_album, "type": "artwork"},
                        )
                    )
        if keep_cue:
            for cue in album.cues:
                destination = album_folder / cue.name
                destination, _collision = _resolve_destination(destination, "rename", planned, None)
                if destination is not None:
                    plans.append(
                        MovePlan(
                            source=cue,
                            destination=destination,
                            mode="apply" if apply else "dry-run",
                            media_type="music",
                            metadata={"artist": dest_artist, "album": dest_album, "type": "cue"},
                        )
                    )
        if keep_log:
            for log in album.logs:
                destination = album_folder / log.name
                destination, _collision = _resolve_destination(destination, "rename", planned, None)
                if destination is not None:
                    plans.append(
                        MovePlan(
                            source=log,
                            destination=destination,
                            mode="apply" if apply else "dry-run",
                            media_type="music",
                            metadata={"artist": dest_artist, "album": dest_album, "type": "log"},
                        )
                    )

        if not verbose_plan:
            _print_music_album_summary(
                album_dest=album_folder,
                track_count=len(planned_tracks),
                artwork=keep_art and bool(album.images),
                cue_count=len(album.cues) if keep_cue else 0,
                log_count=len(album.logs) if keep_log else 0,
            )

    if apply and not copy_mode:
        console.print("Warning: move will remove the original files from the source folder.")
        if not _confirm_move(None):
            console.print("Cancelled. No changes were made.")
            raise typer.Exit(code=0)

    report_path = library / ".plexify" / "reports" / f"{now_timestamp()}.json"
    if apply and plans:
        result = _apply_with_progress(plans, copy_mode=copy_mode, on_conflict="rename")
    else:
        result = execute_plans(plans, apply=apply, copy_mode=copy_mode, on_conflict="rename")

    if cleanup_empty_dirs and apply and not copy_mode and plans:
        _prune_empty_dirs(result.moved, source, dry_run=False)

    write_report(report_path, result.moved if apply else plans, "apply" if apply else "dry-run", copy_mode)

    console.print("Summary:")
    console.print(f"Albums: {len(albums)}")
    console.print(f"Planned files: {len(plans)}")
    if errors:
        console.print(f"Warnings: {len(errors)}")
        for error in errors:
            console.print(f"- {rich_escape(error)}")
    console.print(f"Report path: {format_path(report_path)}")

    if result.errors:
        log_event(
            logger,
            "run_finished",
            run_id=run_id,
            command="music",
            status="error",
            planned_count=len(plans),
            error_count=len(result.errors),
        )
        console.print("Errors:")
        for error in result.errors:
            console.print(f"- {rich_escape(error)}")
        raise typer.Exit(code=1)
    if not plans:
        log_event(
            logger,
            "run_finished",
            run_id=run_id,
            command="music",
            status="empty",
            planned_count=0,
            error_count=0,
        )
        raise typer.Exit(code=1)
    log_event(
        logger,
        "run_finished",
        run_id=run_id,
        command="music",
        status="success",
        planned_count=len(plans),
        error_count=0,
    )
    raise typer.Exit(code=0)


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        wizard(log_level="WARNING", log_format="text", log_file=None)


def _infer_library_root_from_report(report: Path) -> Path | None:
    report_resolved = report.resolve(strict=False)
    parent = report_resolved.parent
    if parent.name != "reports":
        return None
    plexify_dir = parent.parent
    if plexify_dir.name != ".plexify":
        return None
    return plexify_dir.parent


@app.command()
def undo(report: Path = typer.Option(None, help="Report path"), library: Path = typer.Option(None, help="Library root")) -> None:
    if report is None:
        if library is None:
            console.print("Provide --report or --library to locate reports.")
            raise typer.Exit(code=2)
        reports_dir = library / ".plexify" / "reports"
        if not reports_dir.exists():
            console.print("No reports directory found.")
            raise typer.Exit(code=2)
        reports = sorted(reports_dir.glob("*.json"), reverse=True)
        if not reports:
            console.print("No reports found.")
            raise typer.Exit(code=2)
        report = reports[0]
    elif library is None:
        inferred_library = _infer_library_root_from_report(report)
        if inferred_library is None:
            console.print("Provide --library when using --report outside '.plexify/reports'.")
            raise typer.Exit(code=2)
        library = inferred_library

    errors = undo_report(report, library_root=library)
    if errors:
        console.print("Undo completed with warnings:")
        for error in errors:
            console.print(f"- {rich_escape(error)}")
        raise typer.Exit(code=1)
    console.print("Undo completed.")
    raise typer.Exit(code=0)


@app.command()
def wizard(
    log_level: str = typer.Option("WARNING", "--log-level", help="Log level: DEBUG/INFO/WARNING/ERROR"),
    log_format: str = typer.Option("text", "--log-format", help="Log format: text/json"),
    log_file: Path = typer.Option(None, "--log-file", help="Optional log file path"),
) -> None:
    console.print("Plexify wizard")
    console.print("Tip: you can drag-and-drop a folder into the terminal to paste its full path.")
    use_log_file = _confirm(
        "Write logs to a file for this run? [y/N]",
        bool(log_file),
        None,
        show_default=False,
    )
    selected_log_file = log_file
    selected_log_level = log_level.upper()
    selected_log_format = log_format
    if use_log_file:
        default_path = str(log_file) if log_file is not None else str(Path(".plexify") / "run.log")
        selected_log_file = Path(_prompt_text("Log file path", default_path, None))
        selected_log_level = _prompt_choice_loop(
            "Log level (debug/info/warning/error)",
            WIZARD_LOG_LEVEL_CHOICES,
            None,
            allow_empty=True,
            error="Enter one of: debug, info, warning, error.",
            default=selected_log_level.lower(),
        )
        selected_log_format = _prompt_choice_loop(
            "Log format (text/json)",
            WIZARD_LOG_FORMAT_CHOICES,
            None,
            allow_empty=True,
            error="Enter one of: text, json.",
            default=selected_log_format,
        )
    else:
        selected_log_file = None

    _initialise_logging(selected_log_level, selected_log_format, selected_log_file)
    log_event(logger, "run_started", run_id=uuid.uuid4().hex, command="wizard")

    choice = _prompt_choice_loop(
        "Organise: (v)ideo or (m)usic",
        WIZARD_ORGANISE_CHOICES,
        None,
        allow_empty=True,
        error="Enter v for video or m for music.",
        default="video",
    )
    if choice == "music":
        _wizard_music(
            log_level=selected_log_level,
            log_format=selected_log_format,
            log_file=selected_log_file,
        )
    else:
        _wizard_video(
            log_level=selected_log_level,
            log_format=selected_log_format,
            log_file=selected_log_file,
        )


def _prompt_non_overlapping_paths(
    *,
    label_source: str,
    label_library: str,
    source_default: Path | None,
    library_default: Path | None,
) -> tuple[Path, Path]:
    source_text = _prompt_path(
        f"{label_source} folder",
        str(source_default) if source_default is not None else None,
        directories_only=True,
    )
    while not source_text.strip():
        console.print("Please enter a folder path.")
        source_text = _prompt_path(
            f"{label_source} folder",
            str(source_default) if source_default is not None else None,
            directories_only=True,
        )
    source = Path(source_text)
    while not source.exists() or not source.is_dir():
        console.print("That path does not exist or is not a folder. Please try again.")
        source_text = _prompt_path(
            f"{label_source} folder",
            str(source_default) if source_default is not None else None,
            directories_only=True,
        )
        while not source_text.strip():
            console.print("Please enter a folder path.")
            source_text = _prompt_path(
                f"{label_source} folder",
                str(source_default) if source_default is not None else None,
                directories_only=True,
            )
        source = Path(source_text)

    while True:
        library_text = _prompt_path(
            f"{label_library} folder",
            str(library_default) if library_default is not None else None,
            directories_only=True,
        )
        while not library_text.strip():
            console.print("Please enter a folder path.")
            library_text = _prompt_path(
                f"{label_library} folder",
                str(library_default) if library_default is not None else None,
                directories_only=True,
            )
        library = Path(library_text)
        if library.exists() and library.is_file():
            console.print("That path is a file. Please choose a folder path.")
            continue
        if not library.exists():
            if _confirm("That folder does not exist. Create it? [Y/n]", True, None, show_default=False):
                library.mkdir(parents=True, exist_ok=True)
            else:
                console.print("Cancelled. No changes were made.")
                raise typer.Exit(code=0)
        ok, reason, suggestion = validate_non_overlapping(source, library)
        if ok:
            return source, library
        console.print(reason)
        if suggestion is not None:
            console.print(f"Suggested {label_library}: {suggestion}")
            library_default = suggestion
        if _confirm(f"Edit {label_source.lower()} instead? [y/N]", False, None, show_default=False):
            source_text = _prompt_path(
                f"{label_source} folder",
                str(source_default) if source_default is not None else None,
                directories_only=True,
            )
            while not source_text.strip():
                console.print("Please enter a folder path.")
                source_text = _prompt_path(
                    f"{label_source} folder",
                    str(source_default) if source_default is not None else None,
                    directories_only=True,
                )
            source = Path(source_text)
            while not source.exists() or not source.is_dir():
                console.print("That path does not exist or is not a folder. Please try again.")
                source_text = _prompt_path(
                    f"{label_source} folder",
                    str(source_default) if source_default is not None else None,
                    directories_only=True,
                )
                while not source_text.strip():
                    console.print("Please enter a folder path.")
                    source_text = _prompt_path(
                        f"{label_source} folder",
                        str(source_default) if source_default is not None else None,
                        directories_only=True,
                    )
                source = Path(source_text)


def _wizard_video(
    *,
    log_level: str = "INFO",
    log_format: str = "text",
    log_file: Path | None = None,
) -> None:
    console.print("This will help you organise video files into a Plex-friendly folder layout.")
    console.print("Tip: for PowerShell tab-complete paths, run organise with --incoming/--library arguments instead.")
    if COMPLETION_ENABLED:
        console.print("Tip: run python -m plexify.cli --install-completion to enable shell autocompletion.")

    incoming_default, library_default = _wizard_defaults("video")
    incoming, library = _prompt_non_overlapping_paths(
        label_source="Incoming",
        label_library="Library",
        source_default=incoming_default,
        library_default=library_default,
    )
    _save_wizard_prefs("video", incoming, library)

    audio_exts = {ext.strip().lstrip(".") for ext in DEFAULT_MUSIC_EXTENSIONS.split(",") if ext.strip()}
    video_exts = {ext.strip().lstrip(".") for ext in DEFAULT_EXTENSIONS_LIST}
    has_audio, has_video = _detect_media_in_path(incoming, audio_exts, video_exts)
    if has_audio and not has_video:
        if _confirm("This looks like music. Switch to music mode? [Y/n]", True, None, show_default=False):
            _wizard_music(
                source_override=incoming,
                library_override=library,
                log_level=log_level,
                log_format=log_format,
                log_file=log_file,
            )
            return

    media_type = _prompt_choice_loop(
        "Media type (movie/tv/both)",
        WIZARD_MEDIA_CHOICES,
        None,
        allow_empty=True,
        error="Enter one of: movie, tv, both.",
        default="movie",
    )

    mode = _prompt_choice_loop(
        "Mode (dry-run/apply)",
        WIZARD_MODE_CHOICES,
        None,
        allow_empty=True,
        error="Enter one of: dry-run, apply.",
        default="dry-run",
    )

    copy_mode = True
    prune_empty_dirs = False
    if mode == "apply":
        copy_choice = _prompt_choice_loop(
            "Copy or move? (copy/move)",
            WIZARD_COPY_CHOICES,
            None,
            allow_empty=True,
            error="Enter one of: copy, move.",
            default="copy",
        )
        copy_mode = copy_choice == "copy"
        if not copy_mode:
            console.print("Warning: move will remove the original files from the incoming folder.")
            prune_empty_dirs = _confirm("Prune empty folders after move? [y/N]", False, None, show_default=False)

    auto_accept = _confirm("Auto-accept high-confidence matches? [Y/n]", True, None, show_default=False)
    while True:
        min_text = _prompt_text("Minimum confidence", str(DEFAULT_MIN_CONFIDENCE), None)
        try:
            min_confidence = float(min_text)
        except ValueError:
            console.print("Enter a number between 0 and 1.")
            continue
        if 0 <= min_confidence <= 1:
            break
        console.print("Enter a number between 0 and 1.")

    use_cache = _confirm("Use cache? [Y/n]", True, None, show_default=False)
    clear_cache = False
    if use_cache:
        clear_cache = _confirm("Clear cache before running? [y/N]", False, None, show_default=False)

    interactive = _confirm("Interactive mode? [Y/n]", True, None, show_default=False)

    command_config = BuildCommandConfig(
        incoming=incoming,
        library=library,
        media_type=media_type,
        mode=mode,
        copy_mode=copy_mode,
        extensions=DEFAULT_EXTENSIONS_LIST,
        min_confidence=min_confidence,
        limit=None,
        interactive=interactive,
        print_tree=False,
        show_enrichment=False,
        yes=auto_accept,
        no_cache=not use_cache,
        cache_file=None,
        clear_cache=clear_cache,
        report=None,
        on_conflict="rename",
        prune_empty_dirs=prune_empty_dirs,
    )
    command = _build_command(command_config)
    console.print("Running:")
    console.print(command)

    organise(
        incoming=incoming,
        library=library,
        mode=mode,
        move=not copy_mode,
        copy=copy_mode,
        extensions=DEFAULT_EXTENSIONS,
        min_confidence=min_confidence,
        cache=None,
        report=None,
        yes=auto_accept,
        limit=None,
        print_tree=False,
        interactive=interactive,
        no_interactive=not interactive,
        media_type=media_type,
        no_cache=not use_cache,
        clear_cache=clear_cache,
        offline=False,
        on_conflict="rename",
        log_level=log_level,
        log_format=log_format,
        log_file=log_file,
        prune_empty_dirs=prune_empty_dirs,
    )


def _wizard_music(
    source_override: Path | None = None,
    library_override: Path | None = None,
    *,
    log_level: str = "INFO",
    log_format: str = "text",
    log_file: Path | None = None,
) -> None:
    console.print("This will help you organise music into a Plex-friendly folder layout.")
    if COMPLETION_ENABLED:
        console.print("Tip: run python -m plexify.cli --install-completion to enable shell autocompletion.")

    if source_override or library_override:
        source_default = source_override
        library_default = library_override
    else:
        source_default, library_default = _wizard_defaults("music")
    source, library = _prompt_non_overlapping_paths(
        label_source="Source",
        label_library="Library",
        source_default=source_default,
        library_default=library_default,
    )
    _save_wizard_prefs("music", source, library)

    audio_exts = {ext.strip().lstrip(".") for ext in DEFAULT_MUSIC_EXTENSIONS.split(",") if ext.strip()}
    video_exts = {ext.strip().lstrip(".") for ext in DEFAULT_EXTENSIONS_LIST}
    has_audio, has_video = _detect_media_in_path(source, audio_exts, video_exts)
    if has_video and not has_audio:
        if _confirm("This looks like video. Switch to video mode? [Y/n]", True, None, show_default=False):
            _wizard_video(
                log_level=log_level,
                log_format=log_format,
                log_file=log_file,
            )
            return

    mode = _prompt_choice_loop(
        "Mode (dry-run/apply)",
        WIZARD_MODE_CHOICES,
        None,
        allow_empty=True,
        error="Enter one of: dry-run, apply.",
        default="dry-run",
    )

    copy_mode = False
    cleanup_empty_dirs = False
    if mode == "apply":
        copy_mode = _confirm("Copy files instead of moving? [y/N]", False, None, show_default=False)
        if not copy_mode:
            console.print("Warning: move will remove the original files from the source folder.")
            cleanup_empty_dirs = _confirm("Clean up empty folders after move? [y/N]", False, None, show_default=False)

    verify = _confirm("Verify albums with MusicBrainz? [Y/n]", True, None, show_default=False)
    keep_art = _confirm("Keep album artwork? [Y/n]", True, None, show_default=False)
    keep_cue = _confirm("Keep .cue sidecars? [y/N]", False, None, show_default=False)
    keep_log = _confirm("Keep .log sidecars? [y/N]", False, None, show_default=False)
    verbose_plan = _confirm("Show per-track plan? [y/N]", False, None, show_default=False)

    music(
        source=source,
        library=library,
        apply=mode == "apply",
        copy=copy_mode,
        extensions=DEFAULT_MUSIC_EXTENSIONS,
        verify=verify,
        keep_art=keep_art,
        keep_cue=keep_cue,
        keep_log=keep_log,
        offline=False,
        cleanup_empty_dirs=cleanup_empty_dirs,
        verbose_plan=verbose_plan,
        log_level=log_level,
        log_format=log_format,
        log_file=log_file,
    )


if __name__ == "__main__":
    app()

