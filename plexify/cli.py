import re
import sys
import time
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
from .executor import execute_plans
from .infer import InferredItem, infer_item
from .planner import plan_movie, plan_tv_show
from .report import write_report
from .sources import tvmaze, wikidata
from .undo import undo_report
from .util import (
    ExecutionResult,
    MovePlan,
    build_cache_key,
    iter_video_files,
    normalize_title,
    now_timestamp,
    unique_path,
    unique_plan_path,
)

app = typer.Typer(add_completion=False)
console = Console()
DEFAULT_EXTENSIONS = ".mkv,.mp4,.avi,.m4v,.mov,.ts"
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


@dataclass
class PlanStats:
    auto_matched: int = 0
    user_confirmed: int = 0
    manual: int = 0
    skipped: int = 0
    errors: int = 0
    elapsed: float = 0.0


def _console_for(progress: Progress | None) -> Console:
    if progress is not None and hasattr(progress, "console"):
        return progress.console
    return console


def _safe_print(message: str, progress: Progress | None = None) -> None:
    _console_for(progress).print(message)


def _prompt_line(*, has_candidates: bool, allow_search: bool, allow_manual: bool, has_more: bool) -> str:
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
    if has_more:
        parts.append("n=next page")
    return " | ".join(parts) if parts else PROMPT_BASE


def _build_search_query(title: str, hint: str | None) -> str:
    base = normalize_title(title) or title.strip()
    parts = [base]
    if hint:
        hint_text = hint.strip()
        if hint_text:
            parts.append(hint_text)
    return " ".join(part for part in parts if part)


def _with_title(item: InferredItem, title: str) -> InferredItem:
    return InferredItem(
        path=item.path,
        media_type=item.media_type,
        title=title,
        year=item.year,
        season=item.season,
        episode=item.episode,
    )


def _pause_progress(progress: Progress | None) -> bool:
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
    choice = _prompt_choice(prompt, default_text, progress, show_default=show_default)
    return choice in {"y", "yes"}


def _confirm_move(progress: Progress | None) -> bool:
    phrase = _prompt_text("To proceed, type: MOVE", "", progress, show_default=False)
    return phrase.strip().lower() == "move"


def _title_similarity(title_guess: str, title_actual: str) -> float:
    left = normalize_title(title_guess) or title_guess.lower()
    right = normalize_title(title_actual) or title_actual.lower()
    return fuzz.WRatio(left, right) / 100.0


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


def _year_distance(target_year: int | None, candidate_year: int | None) -> int:
    if not target_year or not candidate_year:
        return 999
    return abs(target_year - candidate_year)


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


def _print_candidates(media_type: str, candidates: list[Candidate], progress: Progress | None = None) -> None:
    table = Table(title="Candidates")
    table.add_column("#")
    table.add_column("Title")
    table.add_column("Year")
    show_people = False
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
        row = [str(idx), cand.title, year_text]
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
) -> Candidate | None | str:
    printed_table = False
    while True:
        if candidates and not printed_table:
            _print_candidates(media_type, candidates, progress)
            printed_table = True
        _safe_print(
            _prompt_line(
                has_candidates=bool(candidates),
                allow_search=allow_search,
                allow_manual=allow_manual,
                has_more=has_more,
            ),
            progress,
        )
        choice = _prompt_choice("Select", "", progress, show_default=False)
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
        if choice == "s" and allow_search:
            return "s"
        if choice == "m" and allow_manual:
            return "m"
        if choice in {"k", "q"}:
            return choice
        _safe_print("Invalid choice.", progress)


def _tv_candidates(
    item: InferredItem,
    session: requests.Session,
    cache: Cache,
    show_cache: bool,
    *,
    cache_key: str | None = None,
    offset: int = 0,
    raw_results: list[tvmaze.TVMazeShow] | None = None,
    search_query: str | None = None,
    progress: Progress | None = None,
    limit: int = 5,
) -> CandidatePage:
    cached = cache.get_show(cache_key or item.title)
    results: list[Candidate] = []
    elapsed = 0.0
    if cached and not cached.get("manual"):
        if not cached.get("confirmed_by_user"):
            cached = None
        elif not _cache_entry_compatible(item.year, cached.get("premiered")):
            cached = None
    if cached and not cached.get("manual"):
        if show_cache:
            name = cached.get("name") or item.title
            year = cached.get("premiered")
            year_text = f" ({year})" if year else ""
            _safe_print("Cache hit.", progress)
            _safe_print(f"Using cached match for: {item.path.name} -> {name}{year_text} [TVMaze]", progress)
        show = tvmaze.TVMazeShow(id=int(cached["id"]), name=cached["name"], premiered=cached.get("premiered"))
        results.append(_tv_candidate_from_show(item, show))
        return CandidatePage(candidates=results, raw_results=None, next_offset=0, has_more=False, cache_hit=True)

    if raw_results is None:
        query = search_query or item.title
        _safe_print(f"Searching TVMaze for: {query}", progress)
        started = time.monotonic()
        raw_results = tvmaze.search_shows(query, session=session)
        elapsed = time.monotonic() - started
        if not raw_results:
            _safe_print(f"No candidates ({elapsed:.2f}s).", progress)
            return CandidatePage(candidates=[], raw_results=raw_results, next_offset=0, has_more=False, search_time=elapsed)
    page = raw_results[offset : offset + limit]
    for show in page:
        results.append(_tv_candidate_from_show(item, show))
    results.sort(key=lambda cand: (-cand.confidence, _year_distance(item.year, cand.year)))
    next_offset = offset + limit
    has_more = next_offset < len(raw_results)
    if raw_results is not None and offset == 0:
        best = results[0].confidence if results else 0.0
        _safe_print(f"Found {len(results)} candidates (best confidence {best:.2f}, {elapsed:.2f}s).", progress)
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
    confidence = _confidence_score(item.title, show.name, item.year, year)
    metadata: dict[str, Any] = {"id": show.id, "name": show.name, "year": year}

    return Candidate(title=show.name, year=year, source="TVMaze", confidence=confidence, metadata=metadata)


def _maybe_fetch_episode_title(
    item: InferredItem,
    candidate: Candidate,
    session: requests.Session,
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
    episodes = tvmaze.fetch_episodes(int(show_id), session=session)
    episode_title = None
    for ep in episodes:
        if ep.season == item.season and ep.number == item.episode:
            episode_title = ep.name
            break
    candidate.metadata["episode_title"] = episode_title
    if episode_title and bump_confidence:
        candidate.confidence = min(1.0, candidate.confidence + 0.1)


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
) -> CandidatePage:
    cached = cache.get_movie(cache_key or item.title)
    results: list[Candidate] = []
    elapsed = 0.0
    if cached and not cached.get("manual"):
        if not cached.get("confirmed_by_user"):
            cached = None
        elif not _cache_entry_compatible(item.year, cached.get("year")):
            cached = None
    if cached and not cached.get("manual"):
        if show_cache:
            title = cached.get("title") or item.title
            year = cached.get("year")
            year_text = f" ({year})" if year else ""
            _safe_print("Cache hit.", progress)
            _safe_print(f"Using cached match for: {item.path.name} -> {title}{year_text} [Wikidata]", progress)
        film = wikidata.WikidataFilm(qid=cached["qid"], title=cached["title"], year=cached.get("year"), is_film=True)
        results.append(_movie_candidate_from_film(item, film))
        return CandidatePage(candidates=results, raw_results=None, next_offset=0, has_more=False, cache_hit=True)

    if raw_results is None:
        query = search_query or item.title
        _safe_print(f"Searching Wikidata for: {query}", progress)
        started = time.monotonic()
        raw_results = wikidata.search(query, session=session, limit=10)
        elapsed = time.monotonic() - started
        if not raw_results:
            _safe_print(f"No candidates ({elapsed:.2f}s).", progress)
            return CandidatePage(candidates=[], raw_results=raw_results, next_offset=0, has_more=False, search_time=elapsed)
    idx = offset
    while idx < len(raw_results) and len(results) < limit:
        cand = raw_results[idx]
        idx += 1
        film = wikidata.fetch_entity(cand.qid, session=session)
        if not film.is_film:
            continue
        results.append(_movie_candidate_from_film(item, film, description=cand.description))
    results.sort(key=lambda cand: (-cand.confidence, _year_distance(item.year, cand.year)))
    has_more = idx < len(raw_results)
    if raw_results is not None and offset == 0:
        best = results[0].confidence if results else 0.0
        _safe_print(f"Found {len(results)} candidates (best confidence {best:.2f}, {elapsed:.2f}s).", progress)
    return CandidatePage(candidates=results, raw_results=raw_results, next_offset=idx, has_more=has_more)


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
    episode_title = _prompt_text("Episode title", "", progress)
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
    year_text = _prompt_text("Movie year (optional, helps disambiguate) []:", "", progress, show_default=False)
    hint = _prompt_text("Hint (optional, director/cast/keyword) []:", "", progress, show_default=False)
    year = int(year_text) if year_text else None
    metadata = {"qid": None, "title": title, "year": year, "manual": True}
    return Candidate(title=title, year=year, source="Manual", confidence=1.0, metadata=metadata), hint


def _prompt_search(item: InferredItem, progress: Progress | None) -> tuple[InferredItem, str]:
    query = _prompt_text("Search query", item.title, progress)
    hint = _prompt_text("Hint (optional, director/cast/keyword) []:", "", progress, show_default=False)
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


def _cache_entry_compatible(inferred_year: int | None, cached_year: int | None) -> bool:
    if inferred_year is None or cached_year is None:
        return True
    return _year_distance(inferred_year, cached_year) <= 2


def _auto_acceptable(candidates: list[Candidate], min_confidence: float) -> bool:
    if not candidates:
        return False
    if candidates[0].confidence < min_confidence:
        return False
    if len(candidates) == 1:
        return True
    return (candidates[0].confidence - candidates[1].confidence) >= AUTO_ACCEPT_GAP


def _resolve_destination(
    destination: Path,
    on_conflict: str,
    planned: dict[str, int] | None,
    progress: Progress | None,
) -> tuple[Path | None, bool]:
    changed = False
    if destination.exists():
        if on_conflict == "skip":
            _safe_print(f"Skipping due to existing destination: {destination}", progress)
            return None, False
        if on_conflict == "rename":
            destination = unique_path(destination)
            changed = True
    if planned is None:
        planned = {}
    destination, planned_changed = unique_plan_path(destination, planned)
    changed = changed or planned_changed
    return destination, changed


def _file_panel(index: int, total: int, item: InferredItem) -> Panel:
    title_line = f"File {index}/{total} - {item.media_type.upper()} - {item.path.name}"
    year_text = str(item.year) if item.year else "Unknown"
    lines = [f"Detected: Title={item.title}, Year={year_text}"]
    if item.media_type == "tv":
        season = item.season if item.season is not None else "-"
        episode = item.episode if item.episode is not None else "-"
        lines.append(f"Season/Episode: {season}/{episode}")
    return Panel("\n".join(lines), title=title_line, expand=False)


def _print_plan(plan: MovePlan, progress: Progress | None = None) -> None:
    _safe_print("PLAN", progress)
    _safe_print(f"FROM: {plan.source}", progress)
    _safe_print(f"TO:   {plan.destination}", progress)


def _print_choice(selected: Candidate, progress: Progress | None = None) -> None:
    year_text = str(selected.year) if selected.year else "Unknown"
    _safe_print(f"Chosen: {selected.title} ({year_text}) from {selected.source}", progress)


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
            retry = _prompt_choice("Retry? (y/n)", "y", progress)
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
            child = current_children.get(part)
            if child is None:
                child = current.add(part)
                current_children[part] = child
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
            description = f"{action}: {plan.source.name}"
            progress.update(task, description=description)
            progress.advance(task, 1)

        return execute_plans(plans, apply=True, copy_mode=copy_mode, on_conflict=on_conflict, on_progress=_on_progress)


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
) -> tuple[list[MovePlan], list[str], PlanStats]:
    cache_store: Cache = Cache(cache_path) if use_cache else NullCache()
    exts = [ext.strip() for ext in extensions.split(",") if ext.strip()]
    files = iter_video_files(incoming, exts)
    if limit:
        files = files[:limit]

    plans: list[MovePlan] = []
    errors: list[str] = []
    stats = PlanStats()
    started = time.monotonic()
    planned: dict[str, int] = {}
    collisions = 0

    with Progress(TextColumn("{task.completed}/{task.total} - {task.description}")) as progress:
        task = progress.add_task("Planning files...", total=len(files))
        session_tv = tvmaze.create_session()
        session_wd = wikidata.create_session()
        total = len(files)
        for index, path in enumerate(files, start=1):
            progress.update(task, description=f"Planning: {path.name}")
            progress.advance(task, 1)
            try:
                item = infer_item(path)
                if media_type_filter and item.media_type != media_type_filter:
                    continue
                _safe_print("", progress)
                _safe_print(_file_panel(index, total, item), progress)
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
                    progress=progress,
                    show_cache=show_cache,
                    stats=stats,
                    incoming_root=incoming,
                    planned=planned,
                    on_conflict=on_conflict,
                )
                if plan:
                    plans.append(plan)
                    if collision:
                        collisions += 1
            except Exception as exc:  # noqa: BLE001
                stats.errors += 1
                errors.append(f"{path}: {exc}")

    stats.elapsed = time.monotonic() - started
    if collisions:
        _safe_print(f"{collisions} collision(s) resolved by suffixing (2), (3), ...", None)
    return plans, errors, stats


def _build_command(
    incoming: Path,
    library: Path,
    mode: str,
    copy_mode: bool,
    extensions: str,
    min_confidence: float,
    limit: int | None,
    interactive: bool,
    print_tree: bool,
    auto_accept: bool,
    use_cache: bool,
    media_type_filter: str | None,
    clear_cache: bool,
) -> str:
    parts = [
        "python -m plexify.cli organise",
        f'--incoming "{incoming}"',
        f'--library "{library}"',
        f"--mode {mode}",
    ]
    if mode == "apply":
        parts.append("--copy" if copy_mode else "--move")
    if print_tree:
        parts.append("--print-tree")
    if extensions != DEFAULT_EXTENSIONS:
        parts.append(f'--extensions "{extensions}"')
    if min_confidence != DEFAULT_MIN_CONFIDENCE:
        parts.append(f"--min-confidence {min_confidence}")
    if limit is not None:
        parts.append(f"--limit {limit}")
    if media_type_filter:
        parts.append(f"--media-type {media_type_filter}")
    if auto_accept:
        parts.append("--yes")
    if not use_cache:
        parts.append("--no-cache")
    if clear_cache:
        parts.append("--clear-cache")
    if on_conflict != "rename":
        parts.append(f"--on-conflict {on_conflict}")
    if interactive:
        parts.append("--interactive")
    else:
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
    progress: Progress | None,
    show_cache: bool,
    stats: PlanStats | None = None,
    incoming_root: Path | None = None,
    planned: dict[str, int] | None = None,
    on_conflict: str = "rename",
) -> tuple[MovePlan | None, bool]:
    cache_key = build_cache_key(item.path, incoming_root, item.media_type, item.year)
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
                cache_key=cache_key,
                offset=next_offset,
                raw_results=raw_results_tv,
                search_query=search_query,
                progress=progress,
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
        selected = None
        outcome = None
        while True:
            if not candidates:
                if not interactive:
                    _record_stat(stats, "skipped")
                    return None, False
                _safe_print(f"No candidates found for {item.title}.", progress)
                empty_choice = _select_candidate(
                    "tv",
                    candidates,
                    progress,
                    has_more,
                    allow_search=True,
                    allow_manual=True,
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
                            cache_key=cache_key,
                            offset=next_offset,
                            raw_results=raw_results_tv,
                            search_query=search_query,
                            progress=progress,
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
                continue
            _maybe_fetch_episode_title(item, candidates[0], session_tv, bump_confidence=True)
            if auto_accept and _auto_acceptable(candidates, min_confidence):
                year_text = str(candidates[0].year) if candidates[0].year else "Unknown"
                _safe_print(f"Auto-selected: {candidates[0].title} ({year_text}) [{candidates[0].confidence:.2f}]", progress)
                if not interactive:
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
                        cache_key=cache_key,
                        offset=next_offset,
                        raw_results=raw_results_tv,
                        search_query=search_query,
                        progress=progress,
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
            if choice == "n":
                page = _fetch_with_retry(
                    "TVMaze",
                    lambda: _tv_candidates(
                        item,
                        session_tv,
                        cache,
                        show_cache,
                        cache_key=cache_key,
                        offset=next_offset,
                        raw_results=raw_results_tv,
                        search_query=search_query,
                        progress=progress,
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
            if choice == "m":
                selected = _prompt_manual_tv(item, progress)
                outcome = "manual"
                break
            if choice == "k":
                _record_stat(stats, "skipped")
                return None, False
            if choice == "q":
                raise typer.Exit(code=0)
        if not selected:
            _record_stat(stats, "skipped")
            return None, False
        if selected.metadata.get("manual"):
            outcome = "manual"
        if outcome is None:
            outcome = "confirmed"
        _record_stat(stats, outcome)
        _print_choice(selected, progress)
        _maybe_fetch_episode_title(item, selected, session_tv, bump_confidence=False)
        metadata = selected.metadata
        confirmed_by_user = outcome in {"confirmed", "manual"}
        if selected.metadata.get("manual"):
            cache.set_show(
                cache_key,
                {
                    "id": None,
                    "name": metadata["name"],
                    "premiered": None,
                    "chosen_title": metadata["name"],
                    "chosen_year": metadata.get("year"),
                    "manual": True,
                    "confirmed_by_user": confirmed_by_user,
                    "created_at": now_timestamp(),
                    "source": "Manual",
                },
            )
        else:
            cache.set_show(
                cache_key,
                {
                    "id": metadata["id"],
                    "name": selected.title,
                    "premiered": selected.year,
                    "chosen_title": selected.title,
                    "chosen_year": selected.year,
                    "manual": False,
                    "confirmed_by_user": confirmed_by_user,
                    "created_at": now_timestamp(),
                    "source": selected.source,
                },
            )
        cache.save()

        season = metadata.get("season") or item.season
        episode = metadata.get("episode") or item.episode
        if season is None or episode is None:
            if not interactive:
                return None, False
            season = int(_prompt_text("Season", str(item.season or 1), progress))
            episode = int(_prompt_text("Episode", str(item.episode or 1), progress))
        destination = plan_tv_show(
            library,
            metadata.get("name") or selected.title,
            metadata.get("year") or selected.year,
            int(season),
            int(episode),
            metadata.get("episode_title"),
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
                "episode_title": metadata.get("episode_title"),
            },
        )
        _print_plan(plan, progress)
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
    selected = None
    manual_fallback: Candidate | None = None
    manual_hint = ""
    outcome = None
    while True:
        if not candidates:
            if not interactive:
                _record_stat(stats, "skipped")
                return None, False
            _safe_print(f"No candidates found for {item.title}.", progress)
            empty_choice = _select_candidate(
                "movie",
                candidates,
                progress,
                has_more,
                allow_search=True,
                allow_manual=True,
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
            continue
        if auto_accept and _auto_acceptable(candidates, min_confidence):
            year_text = str(candidates[0].year) if candidates[0].year else "Unknown"
            _safe_print(f"Auto-selected: {candidates[0].title} ({year_text}) [{candidates[0].confidence:.2f}]", progress)
            if not interactive:
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
        cache.set_movie(
            cache_key,
            {
                "qid": None,
                "title": metadata["title"],
                "year": metadata.get("year"),
                "chosen_title": metadata["title"],
                "chosen_year": metadata.get("year"),
                "manual": True,
                "confirmed_by_user": confirmed_by_user,
                "created_at": now_timestamp(),
                "source": "Manual",
            },
        )
    else:
        cache.set_movie(
            cache_key,
            {
                "qid": metadata["qid"],
                "title": selected.title,
                "year": selected.year,
                "chosen_title": selected.title,
                "chosen_year": selected.year,
                "manual": False,
                "confirmed_by_user": confirmed_by_user,
                "created_at": now_timestamp(),
                "source": selected.source,
            },
        )
    cache.save()

    year = metadata.get("year") or selected.year
    if year is None and interactive:
        year_text = _prompt_text("Movie year (optional, helps disambiguate) []:", "", progress, show_default=False)
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
    on_conflict: str = typer.Option("rename", "--on-conflict", help="On destination conflict: rename/skip/overwrite"),
) -> None:
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

    incoming_resolved = incoming.resolve()
    library_resolved = library.resolve()
    overlap = (
        incoming_resolved == library_resolved
        or incoming_resolved.is_relative_to(library_resolved)
        or library_resolved.is_relative_to(incoming_resolved)
    )
    if overlap:
        console.print("Incoming and library folders must not overlap. Use separate folders.")
        raise typer.Exit(code=2)

    interactive_mode = True if interactive else not no_interactive
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
        preview = plans[:5]
        if preview:
            console.print("Preview:")
            for plan in preview:
                console.print(f"FROM: {plan.source}")
                console.print(f"TO:   {plan.destination}")
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

    write_report(report_path, result.moved if apply_mode else plans, mode, copy_mode)
    if result.errors or errors:
        console.print("Errors:")
        for error in result.errors + errors:
            console.print(f"- {error}")
        raise typer.Exit(code=1)
    if not plans:
        raise typer.Exit(code=1)
    raise typer.Exit(code=0)


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        wizard()


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

    errors = undo_report(report)
    if errors:
        console.print("Undo completed with warnings:")
        for error in errors:
            console.print(f"- {error}")
        raise typer.Exit(code=1)
    console.print("Undo completed.")
    raise typer.Exit(code=0)


@app.command()
def wizard() -> None:
    console.print("Plexify wizard")
    console.print("This will help you organise video files into a Plex-friendly folder layout.")

    console.print("Where are the files you want to organise?")
    incoming_default = Path.cwd()
    while True:
        incoming_text = _prompt_text("Incoming folder", str(incoming_default), None)
        incoming = Path(incoming_text)
        if incoming.exists() and incoming.is_dir():
            break
        console.print("That path does not exist or is not a folder. Please try again.")

    console.print("Where should the organised library be created?")
    library_default = incoming.parent / "Library"
    while True:
        library_text = _prompt_text("Library folder", str(library_default), None)
        library = Path(library_text)
        if library.exists() and library.is_file():
            console.print("That path does not exist or is not a folder. Please try again.")
            continue
        if not library.exists():
            if _confirm("That folder does not exist. Create it? [Y/n]: ", True, None, show_default=False):
                library.mkdir(parents=True, exist_ok=True)
                break
            continue
        break

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
            if not _confirm_move(None):
                console.print("Cancelled. No changes were made.")
                raise typer.Exit(code=0)

    auto_accept = _confirm("Auto-accept high-confidence matches? [Y/n]: ", True, None, show_default=False)
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

    use_cache = _confirm("Use cache? [Y/n]: ", True, None, show_default=False)
    clear_cache = False
    if use_cache:
        clear_cache = _confirm("Clear cache before running? [y/N]: ", False, None, show_default=False)

    interactive = _confirm("Interactive mode? [Y/n]: ", True, None, show_default=False)

    command = _build_command(
        incoming,
        library,
        mode,
        copy_mode,
        DEFAULT_EXTENSIONS,
        min_confidence,
        None,
        interactive,
        False,
        auto_accept,
        use_cache,
        media_type,
        clear_cache,
        "rename",
    )
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
    )


if __name__ == "__main__":
    app()
