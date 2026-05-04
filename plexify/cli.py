import os
import re
import sys
import time
import uuid
from collections import Counter
from dataclasses import dataclass
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
from .commands import candidate_flow, confirmations as confirm_cmd
from .commands import music_flow, plan_flow, video_flow, wizard_flow
from .command_builder import build_organise_command
from .executor import execute_plans
from .infer import InferredItem, infer_item
from .logging_config import configure_logging, get_logger, log_event
from .planner import plan_movie, plan_tv_show
from .paths import PathOverlapError, ensure_non_overlapping_paths, validate_non_overlapping
from .prompting import _prompt_text
from . import prompting_ui
from .report import ReportFormatError, open_report_stream, write_report
from .services import (
    movie_matcher,
    music_matcher,
    music_workflow,
    organise_service,
    selection_policy,
    tv_matcher,
    video_item_service,
)
from .sources import musicbrainz, tvmaze, wikidata
from .tv_episode_cache import EpisodeCache
from .undo import undo_report
from .ui import format_path, rich_escape
from . import ui_services
from .runtime_platform import (
    PLEXIFY_PLATFORM_ENV,
    detect_runtime_platform,
    path_lookup_key as runtime_path_lookup_key,
    resolve_platform,
)
from .cache_policy import (
    cache_entry_compatible,
    cache_entry_confirmed_or_auto,
    promote_reusable_with_conflict_tracking,
    reusable_cache_safe,
    should_promote_to_reusable,
    year_distance,
)
from .util import (
    ExecutionResult,
    MovePlan,
    build_cache_key,
    iter_video_files,
    make_search_query,
    movie_cache_key,
    now_timestamp,
    tv_episode_cache_key,
    tv_show_folder_cache_key,
    tv_show_cache_key,
    unique_path,
    unique_plan_path,
)

app = typer.Typer(add_completion=True)
cache_app = typer.Typer(help="Cache maintenance commands")
app.add_typer(cache_app, name="cache")
_FORCE_ASCII_UI = os.getenv("PLEXIFY_ASCII_UI", "").strip().lower() in {"1", "true", "yes", "on"}
_STDOUT_ENCODING = (getattr(sys.stdout, "encoding", None) or "").casefold()
_UTF8_CAPABLE = "utf" in _STDOUT_ENCODING
ASCII_UI_ENABLED = _FORCE_ASCII_UI or not _UTF8_CAPABLE
console = Console(safe_box=ASCII_UI_ENABLED)
logger = get_logger(__name__)
COMPLETION_ENABLED = True
QUIET_OUTPUT = False
PLAIN_OUTPUT = False
CURRENT_EFFECTIVE_PLATFORM = detect_runtime_platform()
_cache_save_warning_shown = False
DEFAULT_EXTENSIONS = ".mkv,.mp4,.avi,.m4v,.mov,.ts"
DEFAULT_EXTENSIONS_LIST = [ext.strip() for ext in DEFAULT_EXTENSIONS.split(",") if ext.strip()]
DEFAULT_MUSIC_EXTENSIONS = "flac,mp3,m4a"
DEFAULT_MIN_CONFIDENCE = 0.90
AUTO_ACCEPT_GAP = 0.08
REUSABLE_PROMOTION_MIN_CONFIDENCE = 0.95
REUSABLE_PROMOTION_MIN_GAP = 0.10
MUSIC_AUTO_ACCEPT_MIN_SCORE = 0.995
MUSIC_AUTO_ACCEPT_MIN_GAP = 0.015
MUSIC_PROMPTLESS_ACCEPT_MIN_SCORE = 0.970
MUSIC_PROMPTLESS_ACCEPT_MIN_GAP = 0.030
MUSIC_MISMATCH_EXTREME_MIN_DIFF = 10
MUSIC_MISMATCH_EXTREME_MIN_RATIO = 1.8
MUSIC_DOMINANT_ARTIST_OVERRIDE_MIN_RATIO = 0.80
MUSIC_DECISION_CACHE_VERSION = 1
MUSIC_UNKNOWN_CLEANUP_CONFIRM_TOKEN = "REMOVE-UNKNOWN"
MUSIC_UNKNOWN_CLEANUP_PREVIEW_LIMIT = 20
DEFAULT_PRUNE_IGNORE = "Thumbs.db,desktop.ini,.DS_Store"
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
WIZARD_MUSIC_PLAN_OUTPUT_CHOICES = {
    "summary": "summary",
    "s": "summary",
    "preview": "preview",
    "p": "preview",
    "full": "full",
    "f": "full",
    "verbose": "full",
}
WIZARD_MUSIC_MISMATCH_CHOICES = {
    "ask": "ask",
    "a": "ask",
    "filename": "filename",
    "f": "filename",
    "filename-titles": "filename-titles",
    "t": "filename-titles",
    "order": "order",
    "o": "order",
}
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
MAX_PLAUSIBLE_EPISODE_NUMBER = 99
MUSIC_FEAT_SUFFIX_RE = re.compile(r"\s+(?:feat\.?|ft\.?|featuring)\s+.*$", re.IGNORECASE)


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
    cache_reusable: bool = False
    search_query_used: str | None = None
    fallback_attempts: int = 0
    attempted_queries: list[str] | None = None
    provider: str | None = None
    lookup_status: str = "ok"
    lookup_reason: str | None = None
    raw_result_count: int | None = None
    candidate_count: int | None = None
    filtered_count: int | None = None


@dataclass
class PlanStats:
    auto_matched: int = 0
    user_confirmed: int = 0
    manual: int = 0
    skipped: int = 0
    filtered_media_type: int = 0
    no_candidates: int = 0
    manual_skip: int = 0
    offline_no_cache: int = 0
    conflict_skip: int = 0
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
    quiet: bool
    prune_ignore: str | None
    allow_risky_enter_accept: bool = False
    strict_safe: bool = False
    plain_output: bool = False
    platform: str = "auto"


@dataclass
class OrganiseOptions:
    incoming: Path
    library: Path
    mode: str
    copy_mode: bool
    extensions: str
    min_confidence: float
    cache: Path | None
    report: Path | None
    yes: bool
    limit: int | None
    print_tree: bool
    interactive_mode: bool
    media_type: str
    no_cache: bool
    clear_cache: bool
    offline: bool
    on_conflict: str
    log_level: str
    log_format: str
    log_file: Path | None
    prune_empty_dirs: bool
    prune_ignore: str
    quiet: bool
    allow_risky_enter_accept: bool = False
    strict_safe: bool = False
    plain_output: bool = False
    platform: str = "auto"


@dataclass(frozen=True)
class MusicPlannedTrack:
    source: Path
    track_number: int
    track_number_text: str
    track_title: str
    track_artist: str
    ext: str
    disc_number: int | None = None


@dataclass(frozen=True)
class MusicAutoDecision:
    action: str
    candidate: musicbrainz.ReleaseCandidate | None
    reason: str


@dataclass(frozen=True)
class CandidatePromptPolicy:
    low_confidence: bool
    risky_reusable_cache_hit: bool
    risky_search_query: bool
    require_explicit_choice: bool


class BackRequested(Exception):
    pass


def _resolve_platform_context(platform: str | None) -> tuple[str, str, str | None]:
    try:
        context = resolve_platform(platform, env=os.environ)
    except ValueError as exc:
        console.print(str(exc))
        raise typer.Exit(code=2) from exc
    return context.requested_platform, context.effective_platform, context.override_source


def _path_lookup_key(path: Path) -> str:
    return runtime_path_lookup_key(path, platform=CURRENT_EFFECTIVE_PLATFORM)


def _console_for(progress: Progress | None) -> Console:
    if progress is not None and hasattr(progress, "console"):
        return progress.console
    return console


def _parse_extensions(extensions: str) -> list[str]:
    return [ext.strip() for ext in extensions.split(",") if ext.strip()]


def _safe_print(message: str, progress: Progress | None = None) -> None:
    if QUIET_OUTPUT:
        return
    _console_for(progress).print(message)


def _save_cache(cache: Cache, progress: Progress | None = None) -> None:
    if cache.save_with_status():
        return
    _warn_cache_busy(progress)


def _warn_cache_busy(progress: Progress | None = None) -> None:
    global _cache_save_warning_shown
    if _cache_save_warning_shown:
        return
    _cache_save_warning_shown = True
    _console_for(progress).print("Warning: cache is busy; this run may proceed without saving some cache updates.")


def _parse_prune_ignore(value: str | None) -> set[str]:
    if not value:
        return set()
    return {token.strip().lower() for token in value.split(",") if token.strip()}


def _tv_search_cache_key(query: str, year: int | None) -> str:
    year_text = str(year) if year is not None else "unknown"
    return f"{query.strip().casefold()}|{year_text}"


def _cache_entry_confirmed_or_auto(entry: dict[str, Any] | None) -> bool:
    return cache_entry_confirmed_or_auto(entry)


def _promote_reusable_with_conflict_tracking(
    media_type: str,
    *,
    cache: Cache,
    key: str,
    entry: dict[str, Any],
) -> None:
    promote_reusable_with_conflict_tracking(media_type, cache=cache, key=key, entry=entry)


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
    return ui_services.switch_item_media_type(item, target_media_type)


def _resolve_media_type_override(
    item: InferredItem,
    cache: Cache,
    incoming_root: Path | None,
    media_type_overrides: dict[str, str] | None,
) -> tuple[InferredItem, str | None]:
    return ui_services.resolve_media_type_override(item, cache, incoming_root, media_type_overrides)


def _persist_media_type_override(
    cache: Cache,
    override_key: str | None,
    media_type: str,
    media_type_overrides: dict[str, str] | None,
    progress: Progress | None = None,
) -> None:
    saved = plan_flow.persist_media_type_override(
        cache=cache,
        override_key=override_key,
        media_type=media_type,
        media_type_overrides=media_type_overrides,
    )
    if not saved:
        _warn_cache_busy(progress)


def _initialise_logging(log_level: str, log_format: str, log_file: Path | None) -> None:
    level = log_level.upper()
    if level not in LOG_LEVELS:
        console.print("Invalid log level. Use DEBUG, INFO, WARNING, or ERROR.")
        raise typer.Exit(code=2)
    if log_format not in LOG_FORMATS:
        console.print("Invalid log format. Use text or json.")
        raise typer.Exit(code=2)
    configure_logging(level=level, fmt=log_format, log_file=log_file)


def _build_search_query(title: str, hint: str | None) -> str:
    return ui_services.build_search_query(title, hint)


def _normalize_tv_retry_query(value: str) -> str:
    return tv_matcher.normalize_tv_retry_query(value, TV_EXPLICIT_SEASON_RE)


def _build_tv_fallback_queries(title: str, hint: str | None, year: int | None = None) -> list[str]:
    return plan_flow.build_tv_fallback_queries(title, hint, year)


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
    return ui_services.apply_tv_folder_season_lock(item, cache, folder_show_key)


def _with_title(item: InferredItem, title: str) -> InferredItem:
    return ui_services.with_title(item, title)


def _strip_outer_quotes(value: str) -> str:
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {'"', "'"}:
        return stripped[1:-1]
    return stripped


def _wizard_prefs_path() -> Path:
    return wizard_flow.wizard_prefs_path()


def _load_wizard_prefs() -> dict[str, dict[str, str]]:
    return wizard_flow.load_wizard_prefs()


def _save_wizard_prefs(media_key: str, source: Path, library: Path) -> None:
    try:
        wizard_flow.save_wizard_prefs(media_key, source, library)
    except OSError:
        log_event(logger, "wizard_prefs_save_failed", level=30, path=_wizard_prefs_path())


def _wizard_defaults(media_key: str) -> tuple[Path | None, Path | None]:
    def _sanitize(path: Path | None) -> Path | None:
        if path is None:
            return None
        try:
            expanded = path.expanduser()
            resolved = expanded.resolve(strict=False)
            if resolved == Path.cwd().resolve(strict=False):
                return None
            if not expanded.exists() or not expanded.is_dir():
                return None
        except (OSError, RuntimeError):
            return None
        return expanded

    prefs = _load_wizard_prefs()
    section = prefs.get(media_key, {})
    source = _sanitize(Path(section["source"])) if "source" in section else None
    library = _sanitize(Path(section["library"])) if "library" in section else None
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
        except (ImportError, OSError):
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


def _prompt_music_track_mismatch_choice(
    progress: Progress | None = None,
    *,
    mismatch_policy: str = "ask",
) -> str:
    if mismatch_policy == "filename":
        return "f"
    if mismatch_policy == "filename-titles":
        return "t"
    if mismatch_policy == "order":
        return "o"
    while True:
        choice = _prompt_choice(
            "r=re-pick release | f=filename titles (original album) | t=filename titles (keep MB album) | o=order",
            "r",
            progress,
            show_default=False,
        )
        if choice in {"r", "f", "t", "o"}:
            return choice
        _safe_print("Enter one of: r, f, t, o.", progress)


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


def _prompt_optional_int(
    prompt: str,
    default: str,
    progress: Progress | None,
    *,
    show_default: bool = True,
) -> int | None:
    while True:
        value = _prompt_text(prompt, default, progress, show_default=show_default).strip()
        if not value:
            return None
        try:
            return int(value)
        except ValueError:
            _safe_print("Please enter a whole number or leave blank.", progress)


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
    return confirm_cmd.confirm_move(
        prompt_text=lambda prompt, default, current_progress: _prompt_text(
            prompt, default, current_progress, show_default=False
        ),
        progress=progress,
    )


def _confirm_overwrite_apply(plans: list[MovePlan], copy_mode: bool) -> bool:
    return confirm_cmd.confirm_overwrite_apply(
        plans=plans,
        copy_mode=copy_mode,
        prompt_text=lambda prompt, default, current_progress: _prompt_text(
            prompt, default, current_progress, show_default=False
        ),
        print_line=console.print,
    )


def _compact_text(value: str) -> str:
    return movie_matcher.compact_text(value)


def _compact_sequel_form(value: str) -> str | None:
    return movie_matcher.compact_sequel_form(value)


def _title_similarity(title_guess: str, title_actual: str) -> float:
    return movie_matcher.title_similarity(title_guess, title_actual)


def _year_adjustment(target_year: int | None, candidate_year: int | None) -> float:
    return movie_matcher.year_adjustment(target_year, candidate_year)


def _confidence_score(title_guess: str, title_actual: str, year_guess: int | None, year_actual: int | None) -> float:
    return movie_matcher.confidence_score(title_guess, title_actual, year_guess, year_actual)


def _tv_confidence_score(title_guess: str, title_actual: str, year_guess: int | None, year_actual: int | None) -> float:
    return tv_matcher.tv_confidence_score(title_guess, title_actual, year_guess, year_actual)


def _year_distance(target_year: int | None, candidate_year: int | None) -> int:
    return year_distance(target_year, candidate_year)


def _has_sequel_marker(title: str) -> bool:
    return movie_matcher.has_sequel_marker(title)


def _search_lost_sequel_marker(title: str, search_query: str) -> bool:
    return movie_matcher.search_lost_sequel_marker(title, search_query)


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
        _save_cache(cache)


def _print_candidates(
    media_type: str,
    candidates: list[Candidate],
    progress: Progress | None = None,
    *,
    item: InferredItem | None = None,
) -> None:
    prompting_ui.print_candidates(
        console=_console_for(progress),
        media_type=media_type,
        candidates=candidates,
        item=item,
        plain=PLAIN_OUTPUT,
    )


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
    allow_enter_accept: bool = True,
) -> Candidate | None | str:
    return candidate_flow.select_candidate(
        media_type=media_type,
        candidates=candidates,
        has_more=has_more,
        allow_search=allow_search,
        allow_manual=allow_manual,
        allow_back=allow_back,
        item=item,
        no_more_results_message=NO_MORE_RESULTS_MESSAGE,
        prompt_base=PROMPT_BASE,
        prompt_choice=lambda prompt, default: _prompt_choice(prompt, default, progress, show_default=False),
        safe_print=lambda message: _safe_print(message, progress),
        print_candidates_fn=lambda mt, cands, current_item: _print_candidates(mt, cands, progress, item=current_item),
        allow_enter_accept=allow_enter_accept,
    )


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
    return video_flow.tv_candidates(
        item=item,
        session=session,
        cache=cache,
        show_cache=show_cache,
        incoming_root=incoming_root,
        cache_key=cache_key,
        offset=offset,
        raw_results=raw_results,
        search_query=search_query,
        progress=progress,
        limit=limit,
        offline=offline,
        interactive=interactive,
        search_cache=search_cache,
        reusable_tv_cache_safe_fn=_reusable_tv_cache_safe,
        tv_show_cache_key_fn=tv_show_cache_key,
        tv_episode_cache_key_fn=tv_episode_cache_key,
        tv_show_folder_cache_key_fn=tv_show_folder_cache_key,
        cache_entry_confirmed_or_auto_fn=_cache_entry_confirmed_or_auto,
        cache_entry_compatible_fn=_cache_entry_compatible,
        log_event_fn=log_event,
        logger=logger,
        safe_print_fn=_safe_print,
        rich_escape_fn=rich_escape,
        candidate_cls=Candidate,
        candidate_page_cls=CandidatePage,
        tv_candidate_from_show_fn=_tv_candidate_from_show,
        make_search_query_fn=make_search_query,
        tv_search_cache_key_fn=_tv_search_cache_key,
        normalize_tv_retry_query_fn=_normalize_tv_retry_query,
        build_tv_fallback_queries_fn=_build_tv_fallback_queries,
        year_distance_fn=_year_distance,
    )


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
    scored = _episode_matches_from_title(item.episode_title, int(show_id), session, episode_cache)
    if not scored:
        return None
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


def _episode_matches_from_title(
    episode_title: str,
    show_id: int,
    session: requests.Session,
    episode_cache: EpisodeCache,
) -> list[tuple[float, tvmaze.TVMazeEpisode]]:
    episodes = episode_cache.get_episodes(int(show_id), session=session)
    if not episodes:
        return []
    scored: list[tuple[float, tvmaze.TVMazeEpisode]] = []
    for ep in episodes:
        if not ep.name:
            continue
        score = fuzz.WRatio(episode_title, ep.name) / 100.0
        scored.append((score, ep))
    if not scored:
        return []
    scored.sort(key=lambda row: row[0], reverse=True)
    return scored


def _auto_resolve_episode_from_title(
    item: InferredItem,
    show_id: int | None,
    session: requests.Session,
    episode_cache: EpisodeCache,
) -> tuple[int, int, str | None] | None:
    if show_id is None or not item.episode_title:
        return None
    scored = _episode_matches_from_title(item.episode_title, int(show_id), session, episode_cache)
    if not scored:
        return None
    top_score, top = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else 0.0
    if top_score < 0.92 or (top_score - second_score) < 0.06:
        return None
    return top.season, top.number, top.name


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
    movie_entity_cache: dict[str, wikidata.WikidataFilm] | None = None,
) -> CandidatePage:
    return video_flow.movie_candidates(
        item=item,
        session=session,
        cache=cache,
        show_cache=show_cache,
        cache_key=cache_key,
        offset=offset,
        raw_results=raw_results,
        search_query=search_query,
        progress=progress,
        limit=limit,
        offline=offline,
        interactive=interactive,
        movie_entity_cache=movie_entity_cache,
        movie_cache_key_fn=movie_cache_key,
        reusable_movie_cache_safe_fn=_reusable_movie_cache_safe,
        cache_entry_confirmed_or_auto_fn=_cache_entry_confirmed_or_auto,
        cache_entry_compatible_fn=_cache_entry_compatible,
        log_event_fn=log_event,
        logger=logger,
        safe_print_fn=_safe_print,
        rich_escape_fn=rich_escape,
        movie_candidate_from_film_fn=_movie_candidate_from_film,
        candidate_page_cls=CandidatePage,
        build_movie_fallback_queries_fn=plan_flow.build_movie_fallback_queries,
        make_search_query_fn=make_search_query,
        year_distance_fn=_year_distance,
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
    year = _prompt_optional_int("Show year", str(item.year) if item.year else "", progress)
    season = _prompt_int("Season", item.season or 1, progress)
    episode = _prompt_int("Episode", item.episode or 1, progress)
    episode_title = _prompt_text("Episode title", item.episode_title or "", progress)
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
    year = _prompt_optional_int(
        "Movie year (optional, helps disambiguate)",
        "",
        progress,
        show_default=False,
    )
    hint = _prompt_text("Hint (optional, director/cast/keyword)", "", progress, show_default=False)
    metadata = {"qid": None, "title": title, "year": year, "manual": True}
    return Candidate(title=title, year=year, source="Manual", confidence=1.0, metadata=metadata), hint


def _prompt_search(item: InferredItem, progress: Progress | None) -> tuple[InferredItem, str]:
    raw_query = _prompt_text("Search query", item.title, progress)
    query = raw_query.strip() or item.title
    hint = _prompt_text("Hint (optional, director/cast/keyword)", "", progress, show_default=False)
    return _with_title(item, query), _build_search_query(query, hint)


def _format_attempted_queries(attempted_queries: list[str] | None) -> str:
    if not attempted_queries:
        return ""
    seen: set[str] = set()
    ordered: list[str] = []
    for query in attempted_queries:
        compact = " ".join(str(query).split()).strip()
        if not compact:
            continue
        marker = compact.casefold()
        if marker in seen:
            continue
        seen.add(marker)
        ordered.append(compact)
    return ", ".join(ordered)


def _announce_attempted_queries(attempted_queries: list[str] | None, progress: Progress | None = None) -> None:
    formatted = _format_attempted_queries(attempted_queries)
    if not formatted:
        return
    _safe_print(f"Already tried: {rich_escape(formatted)}", progress)


def _record_stat(stats: PlanStats | None, outcome: str, *, reason: str | None = None) -> None:
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
        if reason and hasattr(stats, reason):
            setattr(stats, reason, int(getattr(stats, reason, 0)) + 1)


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
        filtered_media_type=stats.filtered_media_type,
        no_candidates=stats.no_candidates,
        manual_skip=stats.manual_skip,
        offline_no_cache=stats.offline_no_cache,
        conflict_skip=stats.conflict_skip,
        errors=stats.errors,
        cache_hits=stats.cache_hits,
        elapsed=stats.elapsed,
    )


def _cache_entry_compatible(inferred_year: int | None, cached_year: int | None) -> bool:
    return cache_entry_compatible(inferred_year, cached_year)


def _reusable_cache_safe(title: str, year: int | None) -> bool:
    return reusable_cache_safe(title, year)


def _reusable_movie_cache_safe(item: InferredItem) -> bool:
    return _reusable_cache_safe(item.title, item.year)


def _reusable_tv_cache_safe(item: InferredItem) -> bool:
    return _reusable_cache_safe(item.title, item.year)


def _should_promote_to_reusable(
    *,
    selection_mode: str | None,
    selected: Candidate,
    candidates: list[Candidate],
) -> bool:
    top_gap = candidates[0].confidence - candidates[1].confidence if len(candidates) > 1 else 0.0
    return selection_policy.should_promote_candidate_to_reusable(
        selection_mode=selection_mode,
        manual=bool(selected.metadata.get("manual")),
        confidence=selected.confidence,
        candidates_count=len(candidates),
        top_gap=top_gap,
        min_confidence=REUSABLE_PROMOTION_MIN_CONFIDENCE,
        min_gap=REUSABLE_PROMOTION_MIN_GAP,
    )


def _auto_acceptable(
    candidates: list[Candidate],
    min_confidence: float,
    *,
    title: str,
    search_query: str,
    target_year: int | None,
) -> bool:
    return selection_policy.auto_acceptable(
        candidates,
        min_confidence,
        title=title,
        search_query=search_query,
        target_year=target_year,
        auto_acceptable_fn=ui_services.auto_acceptable,
    )


def _reusable_cache_hit_looks_risky(item: InferredItem, candidates: list[Candidate], min_confidence: float) -> bool:
    if not candidates:
        return False
    return video_flow.reusable_cache_hit_looks_risky(item, candidates[0].confidence, min_confidence)


def _maybe_auto_select_candidate(
    *,
    candidates: list[Candidate],
    auto_accept: bool,
    min_confidence: float,
    title: str,
    search_query: str,
    target_year: int | None,
    progress: Progress | None,
) -> Candidate | None:
    if not auto_accept or not _auto_acceptable(
        candidates,
        min_confidence,
        title=title,
        search_query=search_query,
        target_year=target_year,
    ):
        return None
    year_text = str(candidates[0].year) if candidates[0].year else "Unknown"
    _safe_print(f"Auto-selected: {candidates[0].title} ({year_text}) [{candidates[0].confidence:.2f}]", progress)
    return candidates[0]


def _candidate_prompt_policy(
    *,
    item: InferredItem,
    candidates: list[Candidate],
    min_confidence: float,
    cache_reusable: bool,
    allow_risky_enter_accept: bool,
    risky_search_query: bool = False,
) -> CandidatePromptPolicy:
    low_confidence = candidates[0].confidence < min_confidence
    risky_reusable_cache_hit = cache_reusable and _reusable_cache_hit_looks_risky(item, candidates, min_confidence)
    policy = selection_policy.build_candidate_prompt_policy(
        low_confidence=low_confidence,
        risky_reusable_cache_hit=risky_reusable_cache_hit,
        risky_search_query=risky_search_query,
        allow_risky_enter_accept=allow_risky_enter_accept,
    )
    return CandidatePromptPolicy(
        low_confidence=policy.low_confidence,
        risky_reusable_cache_hit=policy.risky_reusable_cache_hit,
        risky_search_query=policy.risky_search_query,
        require_explicit_choice=policy.require_explicit_choice,
    )


def _announce_candidate_prompt_policy(
    *,
    media_type: str,
    item: InferredItem,
    search_query: str,
    candidates: list[Candidate],
    auto_accept: bool,
    allow_risky_enter_accept: bool,
    min_confidence: float,
    cache_reusable: bool,
    policy: CandidatePromptPolicy,
    progress: Progress | None,
) -> None:
    _safe_print(
        "Selection policy: "
        f"auto_accept={'on' if auto_accept else 'off'}, "
        f"allow_risky_enter_accept={'on' if allow_risky_enter_accept else 'off'}, "
        f"min_confidence={min_confidence:.2f}, "
        f"explicit_choice_required={'yes' if policy.require_explicit_choice else 'no'}",
        progress,
    )
    if policy.low_confidence:
        _safe_print(
            f"Low confidence ({candidates[0].confidence:.2f} < {min_confidence:.2f}). "
            "Choose explicitly with 1-9, or choose s/m/k/q.",
            progress,
        )
    if policy.risky_search_query:
        _safe_print(
            "Effective search query broadened the title. Review results explicitly with 1-9, or choose s/m/k/q.",
            progress,
        )
    if policy.require_explicit_choice:
        log_event(
            logger,
            "risky_candidate_prompted",
            media_type=media_type,
            path=item.path,
            title=item.title,
            query=search_query,
            selection_mode=None,
            selection_source="interactive",
            decision_reason="risky_candidate_requires_explicit_choice",
            cache_reusable=cache_reusable,
            top_confidence=candidates[0].confidence,
            min_confidence=min_confidence,
            confidence=candidates[0].confidence,
            cache_scope=media_type,
            risky_search_query=policy.risky_search_query,
        )


def _log_explicit_risky_candidate_accept(
    *,
    media_type: str,
    item: InferredItem,
    selected: Candidate,
    search_query: str,
) -> None:
    log_event(
        logger,
        "risky_candidate_explicitly_accepted",
        media_type=media_type,
        path=item.path,
        title=item.title,
        query=search_query,
        selection_mode="confirmed",
        selection_source=selected.source,
        decision_reason="explicit_accept_risky_candidate",
        confidence=selected.confidence,
        cache_scope=media_type,
    )


def _resolve_destination(
    destination: Path,
    on_conflict: str,
    planned: dict[str, int] | None,
    progress: Progress | None,
    platform: str | None = None,
) -> tuple[Path | None, bool]:
    original_destination = destination
    active_platform = platform or CURRENT_EFFECTIVE_PLATFORM
    destination, changed = ui_services.resolve_destination(
        destination,
        on_conflict,
        planned,
        platform=active_platform,
    )
    if destination is None and on_conflict == "skip":
        _safe_print(f"Skipping due to existing destination: {format_path(original_destination)}", progress)
    return destination, changed


def _file_panel(index: int, total: int, item: InferredItem, incoming_root: Path | None) -> Panel | str:
    return prompting_ui.file_panel(index, total, item, incoming_root, plain=PLAIN_OUTPUT)


def _album_panel(index: int, total: int, album: music_util.AlbumGroup) -> Panel | str:
    return prompting_ui.album_panel(index, total, album, plain=PLAIN_OUTPUT)


def _print_music_candidates(candidates: list[musicbrainz.ReleaseCandidate]) -> None:
    prompting_ui.print_music_candidates(console=console, candidates=candidates)


def _rank_music_candidates(
    candidates: list[musicbrainz.ReleaseCandidate],
    track_count: int,
    requested_title: str,
    requested_year: int | None,
) -> list[musicbrainz.ReleaseCandidate]:
    return ui_services.rank_music_candidates(candidates, track_count, requested_title, requested_year)


def _select_music_candidate(
    candidates: list[musicbrainz.ReleaseCandidate],
) -> musicbrainz.ReleaseCandidate | None | str:
    return prompting_ui.select_music_candidate(
        candidates=candidates,
        prompt_choice=lambda prompt, default: _prompt_choice(prompt, default, None, show_default=False),
        safe_print=lambda message: _safe_print(message, None),
        print_music_candidates_fn=_print_music_candidates,
    )


def _music_tracks_from_filenames(
    tracks: list[music_util.TrackInfo],
    *,
    disc_number: int | None = None,
    multi_disc: bool = False,
) -> list[MusicPlannedTrack]:
    return [
        MusicPlannedTrack(
            source=track.source,
            track_number=track.track_number,
            track_number_text=track.track_number_text,
            track_title=track.track_title,
            track_artist=track.track_artist,
            ext=track.ext,
            disc_number=track.disc_number,
        )
        for track in ui_services.music_tracks_from_filenames(tracks, disc_number=disc_number, multi_disc=multi_disc)
    ]


def _map_musicbrainz_tracks(
    tracks: list[music_util.TrackInfo],
    mb_tracks: list[musicbrainz.Track],
) -> tuple[list[MusicPlannedTrack] | None, str | None]:
    mapped, reason = ui_services.map_musicbrainz_tracks(tracks, mb_tracks)
    if mapped is None:
        return None, reason
    return [
        MusicPlannedTrack(
            source=track.source,
            track_number=track.track_number,
            track_number_text=track.track_number_text,
            track_title=track.track_title,
            track_artist=track.track_artist,
            ext=track.ext,
            disc_number=track.disc_number,
        )
        for track in mapped
    ], None


def _map_musicbrainz_by_order(
    tracks: list[music_util.TrackInfo],
    mb_tracks: list[musicbrainz.Track],
) -> list[MusicPlannedTrack]:
    return [
        MusicPlannedTrack(
            source=track.source,
            track_number=track.track_number,
            track_number_text=track.track_number_text,
            track_title=track.track_title,
            track_artist=track.track_artist,
            ext=track.ext,
            disc_number=track.disc_number,
        )
        for track in ui_services.map_musicbrainz_by_order(tracks, mb_tracks)
    ]


def _primary_artist_name(value: str) -> str:
    cleaned = MUSIC_FEAT_SUFFIX_RE.sub("", value or "")
    return " ".join(cleaned.split()).strip()


def _normalise_artist_key(value: str | None) -> str:
    return ui_services.normalise_artist_key(value)


def _dominant_track_artist_ratio(album: music_util.AlbumGroup) -> float:
    counts: Counter[str] = Counter()
    total = 0
    for track in album.tracks:
        key = _normalise_artist_key(track.track_artist)
        if not key:
            continue
        total += 1
        counts[key] += 1
    if not total or not counts:
        return 0.0
    return counts.most_common(1)[0][1] / total


def _should_use_various_artists(album: music_util.AlbumGroup, candidate_artist: str | None) -> bool:
    return ui_services.should_use_various_artists(album, candidate_artist)


def _music_track_count_diff(file_count: int, release_count: int | None) -> int:
    if release_count is None:
        return 10_000
    return abs(file_count - release_count)


def _music_mismatch_is_extreme(file_count: int, release_count: int) -> bool:
    if file_count <= 0 or release_count <= 0:
        return False
    diff = abs(file_count - release_count)
    larger = max(file_count, release_count)
    smaller = min(file_count, release_count)
    ratio = larger / smaller
    return diff >= MUSIC_MISMATCH_EXTREME_MIN_DIFF and ratio >= MUSIC_MISMATCH_EXTREME_MIN_RATIO


def _music_auto_verification_decision(
    candidates: list[musicbrainz.ReleaseCandidate],
    *,
    file_track_count: int,
) -> MusicAutoDecision | None:
    if not candidates:
        return None
    top = candidates[0]
    top_track_count = top.track_count
    if top_track_count is not None and _music_mismatch_is_extreme(file_track_count, top_track_count):
        for candidate in candidates[1:]:
            if candidate.track_count != file_track_count:
                continue
            if candidate.score < MUSIC_AUTO_ACCEPT_MIN_SCORE:
                continue
            return MusicAutoDecision(
                action="accept",
                candidate=candidate,
                reason=(
                    "Top MusicBrainz release has a very large track-count mismatch "
                    f"(files={file_track_count}, release={top_track_count}). "
                    "Auto-selected next exact track-count match "
                    f"(rank={candidate.score:.3f})."
                ),
            )
        return MusicAutoDecision(
            action="skip",
            candidate=None,
            reason=(
                "Top MusicBrainz release has a very large track-count mismatch "
                f"(files={file_track_count}, release={top_track_count})."
            ),
        )

    if top_track_count is None or top_track_count != file_track_count:
        return None
    second = candidates[1] if len(candidates) > 1 else None
    gap = top.score - second.score if second is not None else top.score
    if top.score >= MUSIC_PROMPTLESS_ACCEPT_MIN_SCORE and gap >= MUSIC_PROMPTLESS_ACCEPT_MIN_GAP:
        return MusicAutoDecision(
            action="accept",
            candidate=top,
            reason=f"Auto-selected top MusicBrainz release (rank={top.score:.3f}, gap={gap:.3f}).",
        )
    return None


def _dominant_non_generic_track_artist(album: music_util.AlbumGroup) -> tuple[str | None, float]:
    counts: Counter[str] = Counter()
    labels: dict[str, str] = {}
    total = 0
    for track in album.tracks:
        display = _primary_artist_name(track.track_artist)
        key = display.casefold()
        if not key:
            continue
        total += 1
        counts[key] += 1
        labels.setdefault(key, display)
    if not total or not counts:
        return None, 0.0
    top_key, top_count = counts.most_common(1)[0]
    if top_key in {"various artists", "va", "various"}:
        return None, top_count / total
    return labels.get(top_key, top_key), top_count / total


def _musicbrainz_skip_or_override(album: music_util.AlbumGroup) -> tuple[bool, str | None, str | None]:
    title_key = (album.album or "").strip().casefold()
    artist_key = (album.artist or "").strip().casefold()
    if title_key in {"untitled", "[untitled]"}:
        return True, None, None
    if artist_key in {"various artists", "va", "various"}:
        dominant_artist, ratio = _dominant_non_generic_track_artist(album)
        if dominant_artist is not None and ratio >= MUSIC_DOMINANT_ARTIST_OVERRIDE_MIN_RATIO:
            return (
                False,
                dominant_artist,
                (
                    "Generic album metadata detected; using dominant track artist "
                    f"'{dominant_artist}' for verification."
                ),
            )
        return (
            False,
            "Various Artists",
            "Generic album metadata detected; searching as 'Various Artists'.",
        )
    return False, None, None


def _normalise_music_decision_entry(entry: dict[str, Any] | None) -> dict[str, Any] | None:
    return ui_services.normalise_music_decision_entry(entry)


def _build_music_decision_payload(
    *,
    selection_mode: str,
    decision: str,
    chosen_mbid: str | None = None,
    chosen_artist: str | None = None,
    chosen_album: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    return music_flow.build_music_decision_payload(
        selection_mode=selection_mode,
        decision=decision,
        cache_version=MUSIC_DECISION_CACHE_VERSION,
        now_timestamp=now_timestamp,
        chosen_mbid=chosen_mbid,
        chosen_artist=chosen_artist,
        chosen_album=chosen_album,
        reason=reason,
    )


def _search_musicbrainz_candidates_with_retry(
    *,
    artist: str,
    album: str,
    year: int | None,
    session: requests.Session | None,
) -> tuple[list[musicbrainz.ReleaseCandidate] | None, str, str, str]:
    search_artist = artist
    search_album = album
    attempted_queries: list[str] = []
    while True:
        attempted_queries.append(f"artist={search_artist} album={search_album}")
        candidates = musicbrainz.search_releases(
            search_artist,
            search_album,
            limit=8,
            session=session,
            year=year,
        )
        if not musicbrainz.is_available():
            return None, "offline", search_artist, search_album
        if candidates:
            return candidates, "ok", search_artist, search_album
        if not sys.stdin.isatty():
            return None, "no_matches", search_artist, search_album
        action = _prompt_choice_loop(
            "No MusicBrainz matches. r=retry edit query | f=filename metadata | s=skip album",
            {"r": "r", "f": "f", "s": "s"},
            None,
            allow_empty=True,
            error="Enter one of: r, f, s.",
            default="r",
        )
        if action == "f":
            return None, "fallback", search_artist, search_album
        if action == "s":
            return None, "skip", search_artist, search_album
        _announce_attempted_queries(attempted_queries)
        search_artist = _prompt_text("Search artist", search_artist, None)
        search_album = _prompt_text("Search album", search_album, None)


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


def _print_music_track_previews(track_plans: list[MovePlan], *, limit: int) -> None:
    if limit <= 0 or not track_plans:
        return
    shown = min(limit, len(track_plans))
    console.print(f"Track preview ({shown}/{len(track_plans)}):")
    for plan in track_plans[:shown]:
        console.print(f"- {rich_escape(plan.source.name)} -> {rich_escape(plan.destination.name)}")
    remaining = len(track_plans) - shown
    if remaining > 0:
        console.print(f"... +{remaining} more track(s)")


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


def _apply_with_progress(
    plans: list[MovePlan],
    copy_mode: bool,
    on_conflict: str,
    on_applied: Callable[[MovePlan], None] | None = None,
) -> ExecutionResult:
    if not plans:
        return execute_plans(plans, apply=True, copy_mode=copy_mode, on_conflict=on_conflict, on_applied=on_applied)
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

        return execute_plans(
            plans,
            apply=True,
            copy_mode=copy_mode,
            on_conflict=on_conflict,
            on_progress=_on_progress,
            on_applied=on_applied,
        )


def _apply_with_streamed_report(
    plans: list[MovePlan],
    copy_mode: bool,
    on_conflict: str,
    report_path: Path,
) -> ExecutionResult:
    stream = open_report_stream(report_path, mode="apply", copy_mode=copy_mode)
    try:
        result = _apply_with_progress(
            plans,
            copy_mode=copy_mode,
            on_conflict=on_conflict,
            on_applied=stream.append,
        )
        stream.finalize()
        return result
    finally:
        stream.close()


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


def _prune_ignorable_files(
    path: Path,
    removed_files: set[Path],
    ignored_files: set[str],
    *,
    dry_run: bool,
) -> None:
    if not ignored_files:
        return
    try:
        entries = list(path.iterdir())
    except OSError:
        return
    for entry in entries:
        if not entry.is_file():
            continue
        if entry in removed_files:
            continue
        if entry.name.casefold() not in ignored_files:
            continue
        if dry_run:
            console.print(f"Would remove ignored file: {format_path(entry)}")
            removed_files.add(entry)
            continue
        try:
            entry.unlink()
            removed_files.add(entry)
        except OSError:
            continue


def _remove_skipped_music_sidecars(
    albums: list[music_util.AlbumGroup],
    *,
    keep_cue: bool,
    keep_log: bool,
) -> tuple[int, list[str]]:
    removed = 0
    warnings: list[str] = []
    targets: list[Path] = []
    seen: set[Path] = set()
    for album in albums:
        if not keep_cue:
            targets.extend(album.cues)
        if not keep_log:
            targets.extend(album.logs)
    for sidecar in targets:
        if sidecar in seen:
            continue
        seen.add(sidecar)
        if not sidecar.exists() or not sidecar.is_file():
            continue
        try:
            sidecar.unlink()
            removed += 1
        except OSError as exc:
            warnings.append(f"Could not remove sidecar {sidecar}: {exc}")
    return removed, warnings


def _collect_music_source_leftovers(albums: list[music_util.AlbumGroup]) -> list[Path]:
    known_suffixes = {".flac", ".mp3", ".m4a", ".jpg", ".jpeg", ".png", ".cue", ".log"}
    leftovers: list[Path] = []
    seen_dirs: set[Path] = set()
    seen_files: set[Path] = set()
    for album in albums:
        album_dir = album.source
        if album_dir in seen_dirs:
            continue
        seen_dirs.add(album_dir)
        if not album_dir.exists() or not album_dir.is_dir():
            continue
        try:
            entries = list(album_dir.iterdir())
        except OSError:
            continue
        for entry in entries:
            if not entry.is_file():
                continue
            if entry.suffix.lower() in known_suffixes:
                continue
            if entry in seen_files:
                continue
            seen_files.add(entry)
            leftovers.append(entry)
    leftovers.sort(key=lambda item: str(item).lower())
    return leftovers


def _preview_music_source_leftovers(leftovers: list[Path], *, limit: int = MUSIC_UNKNOWN_CLEANUP_PREVIEW_LIMIT) -> None:
    if not leftovers:
        console.print("No unknown leftover files detected for cleanup.")
        return
    console.print(f"Unknown leftover files detected: {len(leftovers)}")
    for entry in leftovers[:limit]:
        console.print(f"- {format_path(entry)}")
    remaining = len(leftovers) - min(len(leftovers), limit)
    if remaining > 0:
        console.print(f"... +{remaining} more unknown leftover file(s)")


def _cleanup_music_source_leftovers(
    albums: list[music_util.AlbumGroup],
    *,
    remove_unknown_files: bool,
) -> tuple[int, list[str]]:
    removed = 0
    warnings: list[str] = []
    for entry in _collect_music_source_leftovers(albums):
        if not remove_unknown_files:
            warnings.append(f"Leftover source file prevents cleanup: {entry}")
            continue
        try:
            entry.unlink()
            removed += 1
        except OSError as exc:
            warnings.append(f"Could not remove leftover file {entry}: {exc}")
    return removed, warnings


def _prune_empty_dirs(
    plans: list[MovePlan],
    incoming_root: Path,
    *,
    dry_run: bool,
    ignored_files: set[str] | None = None,
) -> None:
    ignored = ignored_files if ignored_files is not None else _parse_prune_ignore(DEFAULT_PRUNE_IGNORE)
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
            _prune_ignorable_files(current, removed_files, ignored, dry_run=dry_run)
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


def _prune_empty_dirs_full_sweep(
    incoming_root: Path,
    *,
    dry_run: bool,
    ignored_files: set[str] | None = None,
) -> None:
    if not incoming_root.exists() or not incoming_root.is_dir():
        return
    ignored = ignored_files if ignored_files is not None else _parse_prune_ignore(DEFAULT_PRUNE_IGNORE)
    removed_files: set[Path] = set()
    removed_dirs: set[Path] = set()
    try:
        directory_candidates = [entry for entry in incoming_root.rglob("*") if entry.is_dir()]
    except OSError:
        return
    directory_candidates.sort(key=lambda path: len(path.parts), reverse=True)
    for current in directory_candidates:
        if current == incoming_root:
            continue
        if current in removed_dirs:
            continue
        if not current.exists():
            continue
        _prune_ignorable_files(current, removed_files, ignored, dry_run=dry_run)
        if not _dir_empty_after_removals(current, removed_files, removed_dirs):
            continue
        if dry_run:
            console.print(f"Would remove empty folder: {format_path(current)}")
        else:
            try:
                current.rmdir()
            except OSError:
                continue
        removed_dirs.add(current)


def _preview_group_key(plan: MovePlan) -> str:
    return video_flow.preview_group_key(plan)


def _select_preview_plans(plans: list[MovePlan], limit: int = 5) -> list[MovePlan]:
    return video_flow.select_preview_plans(plans, limit=limit)


def _preview_spans_multiple_groups(plans: list[MovePlan]) -> bool:
    return video_flow.preview_spans_multiple_groups(plans)


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
    video_flow.print_run_summary(
        console=console,
        format_path_fn=format_path,
        stats=stats,
        plans=plans,
        errors=errors,
        result=result,
        cache_path=cache_path,
        report_path=report_path,
        apply_report_path=apply_report_path,
    )


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
    allow_risky_enter_accept: bool = False,
) -> tuple[list[MovePlan], list[str], PlanStats]:
    global _cache_save_warning_shown
    _cache_save_warning_shown = False
    return video_flow.plan_items(
        incoming=incoming,
        library=library,
        mode=mode,
        copy_mode=copy_mode,
        interactive=interactive,
        auto_accept=auto_accept,
        min_confidence=min_confidence,
        extensions=extensions,
        cache_path=cache_path,
        limit=limit,
        show_cache=show_cache,
        media_type_filter=media_type_filter,
        use_cache=use_cache,
        on_conflict=on_conflict,
        offline=offline,
        allow_risky_enter_accept=allow_risky_enter_accept,
        parse_extensions_fn=_parse_extensions,
        infer_item_fn=infer_item,
        resolve_media_type_override_fn=_resolve_media_type_override,
        safe_print_fn=_safe_print,
        console_for_fn=_console_for,
        file_panel_fn=_file_panel,
        reusable_tv_cache_safe_fn=_reusable_tv_cache_safe,
        snapshot_stats_fn=_snapshot_stats,
        process_item_fn=_process_item,
        save_cache_fn=_save_cache,
        record_log_event_fn=log_event,
        logger=logger,
        rich_escape_fn=rich_escape,
        progress_cls=Progress,
        text_column_cls=TextColumn,
        back_requested_exc=BackRequested,
        cache_snapshot_cls=CacheSnapshot,
        history_entry_cls=HistoryEntry,
        plan_stats_cls=PlanStats,
        quiet_output=QUIET_OUTPUT,
        path_lookup_key_fn=_path_lookup_key,
    )


def _build_command(config: BuildCommandConfig) -> str:
    return build_organise_command(
        incoming=config.incoming,
        library=config.library,
        media_type=config.media_type,
        mode=config.mode,
        copy_mode=config.copy_mode,
        default_extensions=DEFAULT_EXTENSIONS_LIST,
        extensions=config.extensions,
        default_min_confidence=DEFAULT_MIN_CONFIDENCE,
        min_confidence=config.min_confidence,
        limit=config.limit,
        interactive=config.interactive,
        print_tree=config.print_tree,
        yes=config.yes,
        no_cache=config.no_cache,
        cache_file=config.cache_file,
        clear_cache=config.clear_cache,
        report=config.report,
        on_conflict=config.on_conflict,
        prune_empty_dirs=config.prune_empty_dirs,
        quiet=config.quiet,
        prune_ignore=config.prune_ignore,
        allow_risky_enter_accept=config.allow_risky_enter_accept,
        strict_safe=config.strict_safe,
        plain_output=config.plain_output,
        platform=config.platform,
    )


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
    allow_risky_enter_accept: bool = False,
    media_type_overrides: dict[str, str] | None = None,
    tv_search_cache: dict[str, list[tvmaze.TVMazeShow]] | None = None,
    movie_entity_cache: dict[str, wikidata.WikidataFilm] | None = None,
    requested_media_type: str | None = None,
) -> tuple[MovePlan | None, bool]:
    return video_item_service.process_video_item(
        item=item,
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
        allow_risky_enter_accept=allow_risky_enter_accept,
        media_type_overrides=media_type_overrides,
        tv_search_cache=tv_search_cache,
        movie_entity_cache=movie_entity_cache,
        requested_media_type=requested_media_type,
        helpers=sys.modules[__name__],
        reprocess_item_fn=_process_item,
    )

def _apply_strict_safe_policy(options: OrganiseOptions) -> None:
    if not isinstance(options.strict_safe, bool):
        options.strict_safe = False
    if not options.strict_safe:
        return
    options.yes = False
    options.allow_risky_enter_accept = False
    options.no_cache = True
    options.min_confidence = max(options.min_confidence, 0.95)


def _coerce_bool_flag(value: Any, *, default: bool = False) -> bool:
    return value if isinstance(value, bool) else default


def run_organise(options: OrganiseOptions) -> None:
    incoming = options.incoming
    library = options.library
    mode = options.mode
    copy_mode = options.copy_mode
    extensions = options.extensions
    min_confidence = options.min_confidence
    cache = options.cache
    report = options.report
    yes = options.yes
    limit = options.limit
    print_tree = options.print_tree
    interactive_mode = options.interactive_mode
    media_type = options.media_type
    no_cache = options.no_cache
    clear_cache = options.clear_cache
    offline = options.offline
    quiet = options.quiet
    on_conflict = options.on_conflict
    log_level = options.log_level
    log_format = options.log_format
    log_file = options.log_file
    prune_empty_dirs = options.prune_empty_dirs
    prune_ignore = options.prune_ignore
    allow_risky_enter_accept = options.allow_risky_enter_accept
    strict_safe = options.strict_safe
    plain_output = options.plain_output
    platform = options.platform
    yes = _coerce_bool_flag(yes, default=False)
    print_tree = _coerce_bool_flag(print_tree, default=False)
    interactive_mode = _coerce_bool_flag(interactive_mode, default=True)
    no_cache = _coerce_bool_flag(no_cache, default=False)
    clear_cache = _coerce_bool_flag(clear_cache, default=False)
    offline = _coerce_bool_flag(offline, default=False)
    quiet = _coerce_bool_flag(quiet, default=False)
    prune_empty_dirs = _coerce_bool_flag(prune_empty_dirs, default=False)
    allow_risky_enter_accept = _coerce_bool_flag(allow_risky_enter_accept, default=False)
    plain_output = _coerce_bool_flag(plain_output, default=False)
    if not isinstance(min_confidence, (int, float)):
        min_confidence = DEFAULT_MIN_CONFIDENCE
    if not isinstance(extensions, str):
        extensions = DEFAULT_EXTENSIONS
    requested_platform, effective_platform, platform_override_source = _resolve_platform_context(platform)
    global _cache_save_warning_shown
    _cache_save_warning_shown = False
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
        platform=requested_platform,
        effective_platform=effective_platform,
        detected_platform=detect_runtime_platform(),
        platform_override_source=platform_override_source,
    )

    _apply_strict_safe_policy(options)
    yes = _coerce_bool_flag(options.yes, default=False)
    no_cache = _coerce_bool_flag(options.no_cache, default=False)
    min_confidence = options.min_confidence if isinstance(options.min_confidence, (int, float)) else DEFAULT_MIN_CONFIDENCE
    allow_risky_enter_accept = _coerce_bool_flag(options.allow_risky_enter_accept, default=False)
    strict_safe = _coerce_bool_flag(options.strict_safe, default=False)
    plain_output = _coerce_bool_flag(options.plain_output, default=False)

    if mode not in {"dry-run", "apply"}:
        console.print("Invalid mode. Use dry-run or apply.")
        raise typer.Exit(code=2)
    if media_type not in {"auto", "movie", "tv"}:
        console.print("Invalid media type. Use auto, movie, or tv.")
        raise typer.Exit(code=2)
    if on_conflict not in {"rename", "skip", "overwrite"}:
        console.print("Invalid on-conflict policy. Use rename, skip, or overwrite.")
        raise typer.Exit(code=2)
    if strict_safe:
        console.print("Strict-safe mode enabled: cache disabled, auto-accept disabled, confidence floor set to 0.95.")

    try:
        ensure_non_overlapping_paths(
            incoming,
            library,
            label_source="Incoming",
            label_library="Library",
            platform=effective_platform,
        )
    except PathOverlapError as exc:
        _print_overlap_error(exc)
        raise typer.Exit(code=2)

    if not isinstance(quiet, bool):
        quiet = False
    if not isinstance(prune_ignore, str):
        prune_ignore = DEFAULT_PRUNE_IGNORE
    options.prune_ignore = prune_ignore
    if mode == "dry-run":
        console.print("DRY-RUN: no files will be moved/copied.")
    if offline:
        console.print("Offline mode enabled: network lookups disabled.")
        log_event(logger, "offline_mode_enabled", run_id=run_id, command="organise")
    if not isinstance(copy_mode, bool):
        copy_mode = True

    options.run_id = run_id
    options.build_tree_fn = _build_tree
    options.skip_reason_lines_fn = video_flow.skip_reason_lines
    options.rich_escape_fn = rich_escape
    global QUIET_OUTPUT
    global PLAIN_OUTPUT
    global CURRENT_EFFECTIVE_PLATFORM
    previous_quiet_output = QUIET_OUTPUT
    previous_plain_output = PLAIN_OUTPUT
    previous_effective_platform = CURRENT_EFFECTIVE_PLATFORM
    QUIET_OUTPUT = quiet and not interactive_mode
    PLAIN_OUTPUT = plain_output
    CURRENT_EFFECTIVE_PLATFORM = effective_platform
    try:
        organise_service.run_video_workflow(
            options=options,
            console=console,
            plan_items_fn=_plan_items,
            select_preview_plans_fn=_select_preview_plans,
            preview_spans_multiple_groups_fn=_preview_spans_multiple_groups,
            confirm_move_fn=_confirm_move,
            confirm_fn=_confirm,
            confirm_overwrite_apply_fn=_confirm_overwrite_apply,
            apply_with_streamed_report_fn=_apply_with_streamed_report,
            execute_plans_fn=execute_plans,
            prune_empty_dirs_fn=_prune_empty_dirs,
            parse_prune_ignore_fn=_parse_prune_ignore,
            write_report_fn=write_report,
            print_run_summary_fn=_print_run_summary,
            build_command_config_cls=BuildCommandConfig,
            build_command_fn=_build_command,
            parse_extensions_fn=_parse_extensions,
            format_path_fn=format_path,
            now_timestamp_fn=now_timestamp,
            log_event_fn=log_event,
            logger=logger,
            typer_module=typer,
        )
    finally:
        QUIET_OUTPUT = previous_quiet_output
        PLAIN_OUTPUT = previous_plain_output
        CURRENT_EFFECTIVE_PLATFORM = previous_effective_platform


@app.command()
def organise(
    incoming: Path = typer.Option(..., exists=True, file_okay=False, dir_okay=True, help="Folder to scan"),
    library: Path = typer.Option(..., file_okay=False, dir_okay=True, help="Library root"),
    mode: str = typer.Option("dry-run", help="dry-run or apply"),
    move: bool = typer.Option(False, "--move", help="Move files (overrides default copy)"),
    copy: bool = typer.Option(False, "--copy", help="Copy files (default behaviour for apply)"),
    extensions: str = typer.Option(DEFAULT_EXTENSIONS, help="Comma-separated extensions"),
    min_confidence: float = typer.Option(
        DEFAULT_MIN_CONFIDENCE,
        help="Minimum confidence for unambiguous auto acceptance",
    ),
    cache: Path = typer.Option(None, help="Cache path"),
    report: Path = typer.Option(None, help="Report path"),
    yes: bool = typer.Option(
        False,
        "--yes",
        help="Auto-accept unambiguous top result when confidence >= 0.90",
    ),
    limit: int = typer.Option(None, help="Limit number of files"),
    print_tree: bool = typer.Option(False, "--print-tree", help="Print planned destination tree"),
    interactive: bool = typer.Option(False, "--interactive", help="Force interactive mode"),
    no_interactive: bool = typer.Option(False, "--no-interactive", help="Disable interactive prompts"),
    media_type: str = typer.Option("auto", "--media-type", help="Filter by media type: auto/movie/tv"),
    no_cache: bool = typer.Option(False, "--no-cache", help="Disable cache reads/writes"),
    clear_cache: bool = typer.Option(False, "--clear-cache", help="Clear cache before running"),
    offline: bool = typer.Option(False, "--offline", help="Disable network lookups for this run"),
    quiet: bool = typer.Option(False, "--quiet", "--batch", help="Reduce per-file output; show errors and summary"),
    on_conflict: str = typer.Option("rename", "--on-conflict", help="On destination conflict: rename/skip/overwrite"),
    log_level: str = typer.Option("WARNING", "--log-level", help="Log level: DEBUG/INFO/WARNING/ERROR"),
    log_format: str = typer.Option("text", "--log-format", help="Log format: text/json"),
    log_file: Path = typer.Option(None, "--log-file", help="Optional log file path"),
    prune_empty_dirs: bool = typer.Option(False, "--prune-empty-dirs", help="Remove empty folders after move"),
    prune_ignore: str = typer.Option(
        DEFAULT_PRUNE_IGNORE,
        "--prune-ignore",
        help="Comma-separated ignorable filenames for prune-empty-dirs",
    ),
    allow_risky_enter_accept: bool = typer.Option(
        False,
        "--allow-risky-enter-accept",
        help="Allow Enter to accept top match in risky candidate prompts",
    ),
    strict_safe: bool = typer.Option(
        False,
        "--strict-safe",
        help="Use conservative matching defaults (disable cache reuse, disable auto-accept, higher confidence floor)",
    ),
    plain_output: bool = typer.Option(
        False,
        "--plain-output",
        help="Use transcript-friendly plain text output instead of Rich panels and tables",
    ),
    platform: str = typer.Option(
        "auto",
        "--platform",
        help=f"Platform mode: auto/windows/linux (env: {PLEXIFY_PLATFORM_ENV})",
    ),
) -> None:
    """Organise video files for Plex.

    Logging flags: --log-level, --log-format, --log-file.
    """
    move = _coerce_bool_flag(move, default=False)
    copy = _coerce_bool_flag(copy, default=False)
    interactive = _coerce_bool_flag(interactive, default=False)
    no_interactive = _coerce_bool_flag(no_interactive, default=False)
    yes = _coerce_bool_flag(yes, default=False)
    print_tree = _coerce_bool_flag(print_tree, default=False)
    no_cache = _coerce_bool_flag(no_cache, default=False)
    clear_cache = _coerce_bool_flag(clear_cache, default=False)
    offline = _coerce_bool_flag(offline, default=False)
    quiet = _coerce_bool_flag(quiet, default=False)
    prune_empty_dirs = _coerce_bool_flag(prune_empty_dirs, default=False)
    allow_risky_enter_accept = _coerce_bool_flag(allow_risky_enter_accept, default=False)
    strict_safe = _coerce_bool_flag(strict_safe, default=False)
    plain_output = _coerce_bool_flag(plain_output, default=False)

    if move and copy:
        console.print("Choose only one of --move or --copy.")
        raise typer.Exit(code=2)
    if interactive and no_interactive:
        console.print("Choose only one of --interactive or --no-interactive.")
        raise typer.Exit(code=2)

    copy_mode = False if move else True
    interactive_mode = True if interactive else not no_interactive
    options = OrganiseOptions(
        incoming=incoming,
        library=library,
        mode=mode,
        copy_mode=copy_mode,
        extensions=extensions,
        min_confidence=min_confidence,
        cache=cache,
        report=report,
        yes=yes,
        limit=limit,
        print_tree=print_tree,
        interactive_mode=interactive_mode,
        media_type=media_type,
        no_cache=no_cache,
        clear_cache=clear_cache,
        offline=offline,
        on_conflict=on_conflict,
        log_level=log_level,
        log_format=log_format,
        log_file=log_file,
        prune_empty_dirs=prune_empty_dirs,
        prune_ignore=prune_ignore,
        quiet=quiet,
        allow_risky_enter_accept=allow_risky_enter_accept,
        strict_safe=strict_safe,
        plain_output=plain_output,
        platform=platform,
    )
    run_organise(options)


@app.command()
def music(
    source: Path = typer.Option(None, "--source", help="Folder containing album directories"),
    library: Path = typer.Option(None, "--library", help="Library root (will contain Music)"),
    apply: bool = typer.Option(False, "--apply/--dry-run", help="Apply changes or dry-run"),
    copy: bool = typer.Option(False, "--copy", help="Copy files instead of moving"),
    extensions: str = typer.Option(DEFAULT_MUSIC_EXTENSIONS, help="Comma-separated extensions"),
    verify: bool = typer.Option(True, "--verify/--no-verify", help="Verify albums via MusicBrainz"),
    keep_art: bool = typer.Option(True, "--keep-art/--no-art", help="Move/copy album artwork to cover.jpg"),
    keep_cue: bool = typer.Option(False, "--keep-cue", help="Keep .cue sidecars"),
    keep_log: bool = typer.Option(False, "--keep-log", help="Keep .log sidecars"),
    offline: bool = typer.Option(False, "--offline", help="Disable network lookups for this run"),
    cleanup_empty_dirs: bool = typer.Option(False, "--cleanup-empty-dirs", help="Remove empty folders after move"),
    cleanup_unknown_files: bool = typer.Option(
        False,
        "--cleanup-unknown-files",
        help="When cleaning up after move, remove non-media leftover files in source album folders",
    ),
    cleanup_unknown_confirm_token: str = typer.Option(
        "",
        "--cleanup-unknown-confirm-token",
        help="Confirmation token required for unknown leftover deletion (use REMOVE-UNKNOWN)",
    ),
    verbose_plan: bool = typer.Option(False, "--verbose-plan", help="Print per-track plan output"),
    plan_preview_tracks: int = typer.Option(
        0,
        "--plan-preview-tracks",
        min=0,
        help="Preview first N planned tracks per album (ignored with --verbose-plan)",
    ),
    mismatch_policy: str = typer.Option(
        "ask",
        "--mismatch-policy",
        help="Mismatch handling for MB track-count conflicts: ask, filename, filename-titles, order",
    ),
    platform: str = typer.Option(
        "auto",
        "--platform",
        help=f"Platform mode: auto/windows/linux (env: {PLEXIFY_PLATFORM_ENV})",
    ),
    log_level: str = typer.Option("WARNING", "--log-level", help="Log level: DEBUG/INFO/WARNING/ERROR"),
    log_format: str = typer.Option("text", "--log-format", help="Log format: text/json"),
    log_file: Path = typer.Option(None, "--log-file", help="Optional log file path"),
) -> None:
    """Organise music files for Plex.

    Logging flags: --log-level, --log-format, --log-file.
    """
    _initialise_logging(log_level, log_format, log_file)
    if not isinstance(plan_preview_tracks, int):
        plan_preview_tracks = 0
    elif plan_preview_tracks < 0:
        plan_preview_tracks = 0
    if not isinstance(mismatch_policy, str):
        mismatch_policy = "ask"
    mismatch_policy = (mismatch_policy or "ask").strip().lower()
    if not isinstance(cleanup_unknown_confirm_token, str):
        cleanup_unknown_confirm_token = ""
    cleanup_unknown_confirm_token = cleanup_unknown_confirm_token.strip()
    if mismatch_policy not in {"ask", "filename", "filename-titles", "order"}:
        console.print("mismatch-policy must be one of: ask, filename, filename-titles, order.")
        raise typer.Exit(code=2)
    requested_platform, effective_platform, platform_override_source = _resolve_platform_context(platform)
    source_prompted = source is None
    library_prompted = library is None
    run_id = uuid.uuid4().hex
    log_event(
        logger,
        "run_started",
        run_id=run_id,
        command="music",
        source=source,
        library=library,
        mode="apply" if apply else "dry-run",
        platform=requested_platform,
        effective_platform=effective_platform,
        detected_platform=detect_runtime_platform(),
        platform_override_source=platform_override_source,
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
        ensure_non_overlapping_paths(
            source,
            library,
            label_source="Source",
            label_library="Library",
            platform=effective_platform,
        )
    except PathOverlapError as exc:
        _print_overlap_error(exc)
        raise typer.Exit(code=2)
    if source_prompted or library_prompted:
        _save_wizard_prefs("music", source, library)

    if not apply:
        console.print("DRY-RUN: no files will be moved/copied.")
    copy_mode = copy
    if offline and verify:
        console.print("Offline mode: MusicBrainz verification disabled for this run.")
        verify = False
    cleanup_unknown_requested = cleanup_unknown_files and cleanup_empty_dirs and apply and not copy_mode
    cleanup_unknown_token_valid = cleanup_unknown_confirm_token == MUSIC_UNKNOWN_CLEANUP_CONFIRM_TOKEN
    if cleanup_unknown_requested and not cleanup_unknown_token_valid and not sys.stdin.isatty():
        console.print(
            "cleanup-unknown-files in non-interactive mode requires "
            f"--cleanup-unknown-confirm-token {MUSIC_UNKNOWN_CLEANUP_CONFIRM_TOKEN}."
        )
        raise typer.Exit(code=2)

    albums, errors = music_util.discover_albums(source, _parse_extensions(extensions))
    if not albums:
        console.print("No valid albums found.")
        for error in errors:
            console.print(f"- {rich_escape(error)}")
        raise typer.Exit(code=1)
    album_group_counts: Counter[tuple[str, str, int | None]] = Counter(
        (
            _normalise_artist_key(album.artist),
            (album.album or "").strip().casefold(),
            album.year,
        )
        for album in albums
    )
    music_cache = Cache(library / ".plexify" / "cache.json")
    pending_music_decisions: dict[str, dict[str, Any]] = {}

    mb_disabled_reported = False
    if verify and not musicbrainz.is_available():
        reason = musicbrainz.unavailable_reason() or "offline"
        console.print(f"MusicBrainz disabled: {reason}")
        mb_disabled_reported = True

    mb_session: requests.Session | None = musicbrainz.create_session() if verify else None
    release_track_cache: dict[str, list[musicbrainz.Track]] = {}
    planned: dict[str, int] = {}
    plans: list[MovePlan] = []
    verify_remaining = verify
    verification_stats = {
        "auto_selected": 0,
        "manual_selected": 0,
        "skipped_album": 0,
        "skipped_remaining": 0,
        "filename_fallback": 0,
        "filename_titles_fallback": 0,
        "order_fallback": 0,
    }
    try:
        for idx, album in enumerate(albums, start=1):
            console.print(_album_panel(idx, len(albums), album))
            orig_album_artist = album.artist
            orig_album_title = album.album
            album_artist = orig_album_artist
            album_title = orig_album_title
            album_group_key = (
                _normalise_artist_key(album.artist),
                (album.album or "").strip().casefold(),
                album.year,
            )
            folder_multidisc = album.disc_number is not None and album_group_counts.get(album_group_key, 0) > 1
            planned_tracks = _music_tracks_from_filenames(
                album.tracks,
                disc_number=album.disc_number,
                multi_disc=folder_multidisc,
            )
            music_decision_key = music_util.album_decision_cache_key(album)
            invalid_track_count = int(getattr(album, "invalid_track_count", 0) or 0)
            verification_result = music_workflow.resolve_album_verification(
                album=album,
                albums=albums,
                idx=idx,
                orig_album_artist=orig_album_artist,
                orig_album_title=orig_album_title,
                album_artist=album_artist,
                album_title=album_title,
                planned_tracks=planned_tracks,
                folder_multidisc=folder_multidisc,
                verify=verify,
                verify_remaining=verify_remaining,
                mismatch_policy=mismatch_policy,
                music_cache=music_cache,
                music_decision_key=music_decision_key,
                release_track_cache=release_track_cache,
                verification_stats=verification_stats,
                mb_session=mb_session,
                mb_disabled_reported=mb_disabled_reported,
                console=console,
                helpers=sys.modules[__name__],
            )
            planned_tracks = verification_result.planned_tracks
            album_artist = verification_result.album_artist
            album_title = verification_result.album_title
            music_decision_payload = verification_result.music_decision_payload
            verify_remaining = verification_result.verify_remaining
            mb_disabled_reported = verification_result.mb_disabled_reported

            if verify and music_decision_payload is not None:
                music_decision_payload["invalid_track_count"] = invalid_track_count
                pending_music_decisions[music_decision_key] = music_decision_payload

            dest_artist = "Various Artists" if _should_use_various_artists(album, album_artist) else album_artist
            dest_album = album_title

            album_track_plans: list[MovePlan] = []
            for track in planned_tracks:
                destination = music_util.track_destination(
                    library,
                    dest_artist,
                    dest_album,
                    track.track_number_text,
                    track.track_title,
                    track.ext,
                )
                destination, _collision = _resolve_destination(
                    destination, "rename", planned, None, platform=effective_platform
                )
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
                album_track_plans.append(plan)
                if verbose_plan:
                    _print_plan(plan, None)

            album_folder = music_util.album_destination(library, dest_artist, dest_album)
            if keep_art:
                artwork = music_util.select_best_artwork(album.images)
                if artwork:
                    destination = album_folder / "cover.jpg"
                    destination, _collision = _resolve_destination(
                        destination, "rename", planned, None, platform=effective_platform
                    )
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
                    destination, _collision = _resolve_destination(
                        destination, "rename", planned, None, platform=effective_platform
                    )
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
                    destination, _collision = _resolve_destination(
                        destination, "rename", planned, None, platform=effective_platform
                    )
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
                if plan_preview_tracks > 0:
                    _print_music_track_previews(album_track_plans, limit=plan_preview_tracks)
    finally:
        if mb_session is not None:
            mb_session.close()

    if pending_music_decisions:
        with music_cache.batch():
            for decision_key, payload in pending_music_decisions.items():
                music_cache.set_music(decision_key, payload)

    unknown_leftovers: list[Path] = []
    unknown_leftovers_kept = 0
    if cleanup_unknown_requested:
        unknown_leftovers = _collect_music_source_leftovers(albums)
        _preview_music_source_leftovers(unknown_leftovers)
        if not cleanup_unknown_token_valid:
            entered_token = _prompt_text(
                f"Type {MUSIC_UNKNOWN_CLEANUP_CONFIRM_TOKEN} to delete unknown leftovers (press Enter to keep)",
                "",
                None,
                show_default=False,
            ).strip()
            cleanup_unknown_token_valid = entered_token == MUSIC_UNKNOWN_CLEANUP_CONFIRM_TOKEN
        if not cleanup_unknown_token_valid:
            cleanup_unknown_files = False
            unknown_leftovers_kept = len(unknown_leftovers)
            console.print("Unknown leftover cleanup not confirmed; leftovers will be kept.")

    if apply and not copy_mode:
        console.print("Warning: move will remove the original files from the source folder.")
        if not _confirm_move(None):
            console.print("Cancelled. No changes were made.")
            raise typer.Exit(code=0)

    report_path = library / ".plexify" / "reports" / f"{now_timestamp()}.json"
    if apply and plans:
        result = _apply_with_streamed_report(plans, copy_mode=copy_mode, on_conflict="rename", report_path=report_path)
    else:
        result = execute_plans(plans, apply=apply, copy_mode=copy_mode, on_conflict="rename")

    if cleanup_empty_dirs and apply and not copy_mode and plans:
        removed_sidecars, cleanup_warnings = _remove_skipped_music_sidecars(
            albums,
            keep_cue=keep_cue,
            keep_log=keep_log,
        )
        removed_unknown, unknown_warnings = (0, [])
        if cleanup_unknown_files:
            removed_unknown, unknown_warnings = _cleanup_music_source_leftovers(
                albums,
                remove_unknown_files=True,
            )
        if removed_sidecars > 0:
            console.print(f"Removed skipped sidecars: {removed_sidecars}")
        if removed_unknown > 0:
            console.print(f"Removed unknown leftover files: {removed_unknown}")
        if unknown_leftovers_kept > 0:
            console.print(f"Unknown leftovers kept: {unknown_leftovers_kept}")
        if cleanup_warnings:
            errors.extend(cleanup_warnings)
        if unknown_warnings:
            errors.extend(unknown_warnings)
        _prune_empty_dirs(result.moved, source, dry_run=False)
        _prune_empty_dirs_full_sweep(source, dry_run=False)

    if not apply:
        write_report(report_path, plans, "dry-run", copy_mode)
    elif not plans:
        write_report(report_path, [], "apply", copy_mode)

    console.print("Summary:")
    console.print(f"Albums: {len(albums)}")
    console.print(f"Planned files: {len(plans)}")
    if verify:
        console.print(
            "Verification decisions: "
            f"auto={verification_stats['auto_selected']}, "
            f"manual={verification_stats['manual_selected']}, "
            f"skip-album={verification_stats['skipped_album']}, "
            f"skip-remaining={verification_stats['skipped_remaining']}, "
            f"filename-fallback={verification_stats['filename_fallback']}, "
            f"filename-titles-fallback={verification_stats['filename_titles_fallback']}, "
            f"order-fallback={verification_stats['order_fallback']}"
        )
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
            skipped_count=0,
            error_count=len(result.errors),
            elapsed_seconds=None,
            applied=apply,
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
            skipped_count=0,
            error_count=0,
            elapsed_seconds=None,
            applied=apply,
        )
        raise typer.Exit(code=1)
    log_event(
        logger,
        "run_finished",
        run_id=run_id,
        command="music",
        status="success",
        planned_count=len(plans),
        skipped_count=0,
        error_count=0,
        elapsed_seconds=None,
        applied=apply,
    )
    raise typer.Exit(code=0)


def _resolve_cache_path_from_options(cache: Path | None, library: Path | None) -> Path:
    if cache is not None:
        return cache
    if library is not None:
        return library / ".plexify" / "cache.json"
    console.print("Provide --cache or --library.")
    raise typer.Exit(code=2)


@cache_app.command("stats")
def cache_stats(
    cache: Path = typer.Option(None, "--cache", help="Cache path"),
    library: Path = typer.Option(None, "--library", help="Library root (uses .plexify/cache.json)"),
) -> None:
    cache_path = _resolve_cache_path_from_options(cache, library)
    store = Cache(cache_path)
    shows = store.data.get("shows", {})
    movies = store.data.get("movies", {})
    enrichment = store.data.get("enrichment", {})
    ambiguous_shows = sum(1 for entry in shows.values() if isinstance(entry, dict) and entry.get("ambiguous"))
    ambiguous_movies = sum(1 for entry in movies.values() if isinstance(entry, dict) and entry.get("ambiguous"))
    console.print(f"Cache path: {format_path(cache_path)}")
    console.print(f"Shows: {len(shows)}")
    console.print(f"Movies: {len(movies)}")
    console.print(f"Enrichment: {len(enrichment)}")
    console.print(f"Ambiguous shows: {ambiguous_shows}")
    console.print(f"Ambiguous movies: {ambiguous_movies}")
    raise typer.Exit(code=0)


@cache_app.command("prune")
def cache_prune(
    cache: Path = typer.Option(None, "--cache", help="Cache path"),
    library: Path = typer.Option(None, "--library", help="Library root (uses .plexify/cache.json)"),
) -> None:
    cache_path = _resolve_cache_path_from_options(cache, library)
    store = Cache(cache_path)
    shows = dict(store.data.get("shows", {}))
    movies = dict(store.data.get("movies", {}))
    removed_shows = 0
    removed_movies = 0
    for key, entry in list(shows.items()):
        if not isinstance(entry, dict) or not cache_entry_confirmed_or_auto(entry):
            store.delete_show(key)
            removed_shows += 1
    for key, entry in list(movies.items()):
        if not isinstance(entry, dict) or not cache_entry_confirmed_or_auto(entry):
            store.delete_movie(key)
            removed_movies += 1
    if removed_shows or removed_movies:
        store.save()
    console.print(f"Pruned shows: {removed_shows}")
    console.print(f"Pruned movies: {removed_movies}")
    console.print(f"Cache path: {format_path(cache_path)}")
    raise typer.Exit(code=0)


@cache_app.command("delete")
def cache_delete(
    query: str = typer.Argument(..., help="Substring to match cache keys"),
    cache: Path = typer.Option(None, "--cache", help="Cache path"),
    library: Path = typer.Option(None, "--library", help="Library root (uses .plexify/cache.json)"),
) -> None:
    cache_path = _resolve_cache_path_from_options(cache, library)
    store = Cache(cache_path)
    needle = query.strip().casefold()
    if not needle:
        console.print("Query cannot be empty.")
        raise typer.Exit(code=2)
    removed_shows = 0
    removed_movies = 0
    for key in list(store.data.get("shows", {}).keys()):
        if needle in key.casefold():
            store.delete_show(key)
            removed_shows += 1
    for key in list(store.data.get("movies", {}).keys()):
        if needle in key.casefold():
            store.delete_movie(key)
            removed_movies += 1
    if removed_shows or removed_movies:
        store.save()
    console.print(f"Deleted shows: {removed_shows}")
    console.print(f"Deleted movies: {removed_movies}")
    console.print(f"Cache path: {format_path(cache_path)}")
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
def ui(
    log_level: str = typer.Option("WARNING", "--log-level", help="Log level: DEBUG/INFO/WARNING/ERROR"),
    log_format: str = typer.Option("text", "--log-format", help="Log format: text/json"),
    log_file: Path = typer.Option(None, "--log-file", help="Optional log file path"),
) -> None:
    _initialise_logging(log_level, log_format, log_file)
    try:
        from .textual_app import run_textual_ui
    except ImportError as exc:
        console.print("Textual UI dependencies are not installed. Install the project dependencies and try again.")
        raise typer.Exit(code=1) from exc
    run_textual_ui()


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

    try:
        errors = undo_report(report, library_root=library)
    except ReportFormatError as exc:
        console.print(f"Invalid report: {exc}")
        raise typer.Exit(code=2)
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
    platform: str = typer.Option(
        "auto",
        "--platform",
        help=f"Platform mode: auto/windows/linux (env: {PLEXIFY_PLATFORM_ENV})",
    ),
) -> None:
    """Run the interactive setup wizard.

    Logging flags: --log-level, --log-format, --log-file.
    """
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
    requested_platform, effective_platform, platform_override_source = _resolve_platform_context(platform)
    log_event(
        logger,
        "run_started",
        run_id=uuid.uuid4().hex,
        command="wizard",
        platform=requested_platform,
        effective_platform=effective_platform,
        detected_platform=detect_runtime_platform(),
        platform_override_source=platform_override_source,
    )

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
            platform=requested_platform,
        )
    else:
        _wizard_video(
            log_level=selected_log_level,
            log_format=selected_log_format,
            log_file=selected_log_file,
            platform=requested_platform,
        )


def _prompt_non_overlapping_paths(
    *,
    label_source: str,
    label_library: str,
    source_default: Path | None,
    library_default: Path | None,
    platform: str = "auto",
) -> tuple[Path, Path]:
    return wizard_flow.prompt_non_overlapping_paths(
        label_source=label_source,
        label_library=label_library,
        source_default=source_default,
        library_default=library_default,
        prompt_path_fn=_prompt_path,
        confirm_fn=_confirm,
        validate_non_overlapping_fn=lambda source, library: validate_non_overlapping(
            source, library, platform=platform
        ),
        console=console,
        typer_module=typer,
    )


def _wizard_video(
    *,
    log_level: str = "INFO",
    log_format: str = "text",
    log_file: Path | None = None,
    platform: str = "auto",
) -> None:
    return wizard_flow.wizard_video(
        log_level=log_level,
        log_format=log_format,
        log_file=log_file,
        completion_enabled=COMPLETION_ENABLED,
        console=console,
        wizard_defaults_fn=_wizard_defaults,
        prompt_non_overlapping_paths_fn=_prompt_non_overlapping_paths,
        save_wizard_prefs_fn=_save_wizard_prefs,
        detect_media_in_path_fn=_detect_media_in_path,
        confirm_fn=_confirm,
        wizard_music_fn=_wizard_music,
        prompt_choice_loop_fn=_prompt_choice_loop,
        prompt_text_fn=_prompt_text,
        build_command_config_cls=BuildCommandConfig,
        build_command_fn=_build_command,
        organise_options_cls=OrganiseOptions,
        run_organise_fn=run_organise,
        default_music_extensions=DEFAULT_MUSIC_EXTENSIONS,
        default_extensions_list=DEFAULT_EXTENSIONS_LIST,
        default_min_confidence=DEFAULT_MIN_CONFIDENCE,
        wizard_media_choices=WIZARD_MEDIA_CHOICES,
        wizard_mode_choices=WIZARD_MODE_CHOICES,
        wizard_copy_choices=WIZARD_COPY_CHOICES,
        default_extensions=DEFAULT_EXTENSIONS,
        default_prune_ignore=DEFAULT_PRUNE_IGNORE,
        platform=platform,
    )


def _wizard_music(
    source_override: Path | None = None,
    library_override: Path | None = None,
    *,
    log_level: str = "INFO",
    log_format: str = "text",
    log_file: Path | None = None,
    platform: str = "auto",
) -> None:
    return wizard_flow.wizard_music(
        source_override=source_override,
        library_override=library_override,
        log_level=log_level,
        log_format=log_format,
        log_file=log_file,
        completion_enabled=COMPLETION_ENABLED,
        console=console,
        wizard_defaults_fn=_wizard_defaults,
        prompt_non_overlapping_paths_fn=_prompt_non_overlapping_paths,
        save_wizard_prefs_fn=_save_wizard_prefs,
        detect_media_in_path_fn=_detect_media_in_path,
        confirm_fn=_confirm,
        wizard_video_fn=_wizard_video,
        prompt_choice_loop_fn=_prompt_choice_loop,
        prompt_int_fn=_prompt_int,
        music_fn=music,
        default_music_extensions=DEFAULT_MUSIC_EXTENSIONS,
        default_extensions_list=DEFAULT_EXTENSIONS_LIST,
        wizard_mode_choices=WIZARD_MODE_CHOICES,
        wizard_copy_choices=WIZARD_COPY_CHOICES,
        wizard_music_mismatch_choices=WIZARD_MUSIC_MISMATCH_CHOICES,
        wizard_music_plan_output_choices=WIZARD_MUSIC_PLAN_OUTPUT_CHOICES,
        platform=platform,
    )


if __name__ == "__main__":
    app()


