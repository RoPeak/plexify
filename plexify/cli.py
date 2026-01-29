import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

import requests
import typer
from rapidfuzz import fuzz
from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn, TimeRemainingColumn
from rich.prompt import Prompt
from rich.table import Table
from rich.tree import Tree

from .cache import Cache
from .executor import execute_plans
from .infer import InferredItem, infer_item
from .planner import plan_movie, plan_tv_show
from .report import write_report
from .sources import tvmaze, wikidata
from .undo import undo_report
from .util import ExecutionResult, MovePlan, iter_video_files, now_timestamp, unique_path

app = typer.Typer(add_completion=False)
console = Console()
DEFAULT_EXTENSIONS = ".mkv,.mp4,.avi,.m4v,.mov,.ts"
DEFAULT_MIN_CONFIDENCE = 0.55


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


def _confirm(prompt: str, default: bool, progress: Progress | None, show_default: bool = True) -> bool:
    default_text = "y" if default else "n"
    choice = _prompt_choice(prompt, default_text, progress, show_default=show_default)
    return choice in {"y", "yes"}


def _confidence_score(title_guess: str, title_actual: str, year_guess: int | None, year_actual: int | None) -> float:
    base = fuzz.WRatio(title_guess, title_actual) / 100.0
    if year_guess and year_actual and year_guess == year_actual:
        base = min(1.0, base + 0.1)
    return max(0.0, min(1.0, base))


def _format_value(value: str | None) -> str:
    return value if value else "—"


def _format_names(names: list[str] | None, limit: int = 3) -> str:
    if not names:
        return "—"
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
    for cand in candidates[:5]:
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


def _print_candidates(media_type: str, candidates: list[Candidate]) -> None:
    table = Table(title="Candidates")
    table.add_column("#")
    table.add_column("Title")
    table.add_column("Year")
    if media_type == "movie":
        table.add_column("Director")
        table.add_column("Cast")
    elif media_type == "tv":
        table.add_column("Network/Creator")
        table.add_column("Cast")
    table.add_column("Source")
    table.add_column("Confidence")
    for idx, cand in enumerate(candidates, start=1):
        year_text = str(cand.year) if cand.year else "Unknown"
        enrichment = cand.enrichment or {}
        row = [str(idx), cand.title, year_text]
        if media_type == "movie":
            row.append(_format_value(enrichment.get("director")))
            row.append(_format_names(enrichment.get("cast")))
        elif media_type == "tv":
            network = enrichment.get("network") or enrichment.get("creator")
            row.append(_format_value(network))
            row.append(_format_names(enrichment.get("cast")))
        row.extend([cand.source, f"{cand.confidence:.2f}"])
        table.add_row(*row)
    console.print(table)


def _select_candidate(
    media_type: str,
    candidates: list[Candidate],
    progress: Progress | None,
    has_more: bool,
) -> Candidate | None | str:
    if not candidates:
        return None
    instruction = "Press Enter to accept #1, type 1-5 to choose, 's' to search, 'n' for more, 'k' to skip, 'q' to quit."
    while True:
        _print_candidates(media_type, candidates)
        console.print(instruction)
        choice = _prompt_choice("Select >", "1", progress, show_default=False)
        if choice == "1":
            return candidates[0]
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(candidates):
                return candidates[idx]
            console.print("Invalid selection.")
            continue
        if choice == "n":
            if not has_more:
                console.print("No more candidates.")
                continue
            return "n"
        if choice in {"s", "m", "k", "q"}:
            return choice
        console.print("Invalid choice.")


def _tv_candidates(
    item: InferredItem,
    session: requests.Session,
    cache: Cache,
    show_cache: bool,
    *,
    offset: int = 0,
    raw_results: list[tvmaze.TVMazeShow] | None = None,
    limit: int = 5,
) -> CandidatePage:
    cached = cache.get_show(item.title)
    results: list[Candidate] = []
    if cached and not cached.get("manual"):
        if show_cache:
            name = cached.get("name") or item.title
            year = cached.get("premiered")
            year_text = f" ({year})" if year else ""
            console.print(f"Using cached match for: {item.path.name} -> {name}{year_text} [TVMaze]")
        show = tvmaze.TVMazeShow(id=int(cached["id"]), name=cached["name"], premiered=cached.get("premiered"))
        results.append(_tv_candidate_from_show(item, show))
        return CandidatePage(candidates=results, raw_results=None, next_offset=0, has_more=False)

    if raw_results is None:
        raw_results = tvmaze.search_shows(item.title, session=session)
    page = raw_results[offset : offset + limit]
    for show in page:
        results.append(_tv_candidate_from_show(item, show))
    next_offset = offset + limit
    has_more = next_offset < len(raw_results)
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
    confidence = _confidence_score(item.title, show.name, None, None)
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
    offset: int = 0,
    raw_results: list[wikidata.WikidataCandidate] | None = None,
    limit: int = 5,
) -> CandidatePage:
    cached = cache.get_movie(item.title)
    results: list[Candidate] = []
    if cached and not cached.get("manual"):
        if show_cache:
            title = cached.get("title") or item.title
            year = cached.get("year")
            year_text = f" ({year})" if year else ""
            console.print(f"Using cached match for: {item.path.name} -> {title}{year_text} [Wikidata]")
        film = wikidata.WikidataFilm(qid=cached["qid"], title=cached["title"], year=cached.get("year"), is_film=True)
        results.append(_movie_candidate_from_film(item, film))
        return CandidatePage(candidates=results, raw_results=None, next_offset=0, has_more=False)

    if raw_results is None:
        raw_results = wikidata.search(item.title, session=session, limit=10)
    idx = offset
    while idx < len(raw_results) and len(results) < limit:
        cand = raw_results[idx]
        idx += 1
        film = wikidata.fetch_entity(cand.qid, session=session)
        if not film.is_film:
            continue
        results.append(_movie_candidate_from_film(item, film, description=cand.description))
    has_more = idx < len(raw_results)
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


def _prompt_manual_movie(item: InferredItem, progress: Progress | None) -> Candidate:
    title = _prompt_text("Movie title", item.title, progress)
    year_text = _prompt_text("Movie year", str(item.year) if item.year else "", progress)
    year = int(year_text) if year_text else None
    metadata = {"qid": None, "title": title, "year": year, "manual": True}
    return Candidate(title=title, year=year, source="Manual", confidence=1.0, metadata=metadata)


def _print_plan(plan: MovePlan) -> None:
    console.print("PLAN")
    console.print(f"FROM: {plan.source}")
    console.print(f"TO:   {plan.destination}")


def _print_choice(selected: Candidate) -> None:
    year_text = str(selected.year) if selected.year else "Unknown"
    console.print(f"Chosen: {selected.title} ({year_text}) from {selected.source}")


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
            console.print(f"{label} request failed: {exc.__class__.__name__}")
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


def _apply_with_progress(plans: list[MovePlan], copy_mode: bool) -> ExecutionResult:
    if not plans:
        return execute_plans(plans, apply=True, copy_mode=copy_mode)
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

        return execute_plans(plans, apply=True, copy_mode=copy_mode, on_progress=_on_progress)


def _plan_items(
    incoming: Path,
    library: Path,
    mode: str,
    copy_mode: bool,
    interactive: bool,
    yes: bool,
    min_confidence: float,
    extensions: str,
    cache_path: Path,
    limit: int | None,
    show_cache: bool,
) -> tuple[list[MovePlan], list[str]]:
    cache_store = Cache(cache_path)
    exts = [ext.strip() for ext in extensions.split(",") if ext.strip()]
    files = iter_video_files(incoming, exts)
    if limit:
        files = files[:limit]

    plans: list[MovePlan] = []
    errors: list[str] = []

    with Progress(TextColumn("{task.completed}/{task.total} - {task.description}")) as progress:
        task = progress.add_task("Planning files...", total=len(files))
        session_tv = tvmaze.create_session()
        session_wd = wikidata.create_session()
        total = len(files)
        for index, path in enumerate(files, start=1):
            was_running = _pause_progress(progress)
            console.print(f"File {index}/{total}: {path}")
            _resume_progress(progress, was_running)
            progress.update(task, description=f"Planning: {path.name}")
            progress.advance(task, 1)
            try:
                item = infer_item(path)
                plan = _process_item(
                    item=item,
                    library=library,
                    cache=cache_store,
                    mode=mode,
                    copy_mode=copy_mode,
                    interactive=interactive,
                    yes=yes,
                    min_confidence=min_confidence,
                    session_tv=session_tv,
                    session_wd=session_wd,
                    progress=progress,
                    show_cache=show_cache,
                )
                if plan:
                    plans.append(plan)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{path}: {exc}")

    return plans, errors


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
    yes: bool,
    min_confidence: float,
    session_tv: requests.Session,
    session_wd: requests.Session,
    progress: Progress | None,
    show_cache: bool,
) -> MovePlan | None:
    console.print(f"Detected: {item.media_type.upper()} | Title guess: {item.title}")
    if item.media_type == "tv":
        console.print(f"Season/Episode guess: {item.season}/{item.episode}")
    if item.media_type == "tv":
        raw_results_tv: list[tvmaze.TVMazeShow] | None = None
        next_offset = 0
        page = _fetch_with_retry(
            "TVMaze",
            lambda: _tv_candidates(item, session_tv, cache, show_cache, offset=next_offset, raw_results=raw_results_tv),
            interactive,
            progress,
        )
        if page is None:
            return None
        candidates = page.candidates
        raw_results_tv = page.raw_results
        next_offset = page.next_offset
        has_more = page.has_more
        selected = None
        while True:
            if not candidates:
                if interactive:
                    console.print(f"No candidates found for {item.title}.")
                    empty_choice = _prompt_choice("Choose: s (new search), m (manual), k (skip), q (quit): ", "k", progress)
                    if empty_choice == "s":
                        query = _prompt_text("Search query", item.title, progress)
                        item = InferredItem(
                            path=item.path,
                            media_type=item.media_type,
                            title=query,
                            year=item.year,
                            season=item.season,
                            episode=item.episode,
                        )
                        raw_results_tv = None
                        next_offset = 0
                        page = _fetch_with_retry(
                            "TVMaze",
                            lambda: _tv_candidates(item, session_tv, cache, show_cache, offset=next_offset, raw_results=raw_results_tv),
                            interactive,
                            progress,
                        )
                        if page is None:
                            return None
                        candidates = page.candidates
                        raw_results_tv = page.raw_results
                        next_offset = page.next_offset
                        has_more = page.has_more
                        continue
                    if empty_choice == "m":
                        selected = _prompt_manual_tv(item, progress)
                        break
                    if empty_choice == "k":
                        return None
                    if empty_choice == "q":
                        raise typer.Exit(code=0)
                    console.print("Invalid choice.")
                    continue
                selected = None
                break
            _maybe_fetch_episode_title(item, candidates[0], session_tv, bump_confidence=True)
            if candidates[0].confidence < min_confidence and interactive:
                console.print("Top confidence below minimum threshold.")
                low_choice = _prompt_choice(
                    "Choose: s (new search), m (manual), k (skip), q (quit), Enter (review list): ",
                    "",
                    progress,
                )
                if low_choice in {"s", "m", "k", "q"}:
                    if low_choice == "s":
                        query = _prompt_text("Search query", item.title, progress)
                        item = InferredItem(path=item.path, media_type=item.media_type, title=query, year=item.year, season=item.season, episode=item.episode)
                        raw_results_tv = None
                        next_offset = 0
                        page = _fetch_with_retry(
                            "TVMaze",
                            lambda: _tv_candidates(item, session_tv, cache, show_cache, offset=next_offset, raw_results=raw_results_tv),
                            interactive,
                            progress,
                        )
                        if page is None:
                            return None
                        candidates = page.candidates
                        raw_results_tv = page.raw_results
                        next_offset = page.next_offset
                        has_more = page.has_more
                        continue
                    if low_choice == "m":
                        selected = _prompt_manual_tv(item, progress)
                        break
                    if low_choice == "k":
                        return None
                    if low_choice == "q":
                        raise typer.Exit(code=0)
            if yes and candidates[0].confidence >= min_confidence:
                selected = candidates[0]
                break
            if not interactive:
                selected = None
                break
            _maybe_enrich_candidates("tv", candidates, session_tv, session_wd, cache, interactive)
            choice = _select_candidate("tv", candidates, progress, has_more)
            if isinstance(choice, Candidate):
                selected = choice
                break
            if choice == "s":
                query = _prompt_text("Search query", item.title, progress)
                item = InferredItem(path=item.path, media_type=item.media_type, title=query, year=item.year, season=item.season, episode=item.episode)
                raw_results_tv = None
                next_offset = 0
                page = _fetch_with_retry(
                    "TVMaze",
                    lambda: _tv_candidates(item, session_tv, cache, show_cache, offset=next_offset, raw_results=raw_results_tv),
                    interactive,
                    progress,
                )
                if page is None:
                    return None
                candidates = page.candidates
                raw_results_tv = page.raw_results
                next_offset = page.next_offset
                has_more = page.has_more
                continue
            if choice == "n":
                if not has_more:
                    console.print("No more candidates.")
                    continue
                page = _fetch_with_retry(
                    "TVMaze",
                    lambda: _tv_candidates(item, session_tv, cache, show_cache, offset=next_offset, raw_results=raw_results_tv),
                    interactive,
                    progress,
                )
                if page is None:
                    return None
                candidates = page.candidates
                raw_results_tv = page.raw_results
                next_offset = page.next_offset
                has_more = page.has_more
                continue
            if choice == "m":
                selected = _prompt_manual_tv(item, progress)
                break
            if choice == "k":
                return None
            if choice == "q":
                raise typer.Exit(code=0)
        if not selected:
            return None
        _print_choice(selected)
        _maybe_fetch_episode_title(item, selected, session_tv, bump_confidence=False)
        metadata = selected.metadata
        if selected.metadata.get("manual"):
            cache.set_show(item.title, {"id": None, "name": metadata["name"], "premiered": None, "manual": True})
        else:
            cache.set_show(item.title, {"id": metadata["id"], "name": selected.title, "premiered": selected.year, "manual": False})
        cache.save()

        season = metadata.get("season") or item.season
        episode = metadata.get("episode") or item.episode
        if season is None or episode is None:
            if not interactive:
                return None
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
        plan = MovePlan(
            source=item.path,
            destination=unique_path(destination),
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
        _print_plan(plan)
        return plan

    raw_results_movie: list[wikidata.WikidataCandidate] | None = None
    next_offset = 0
    page = _fetch_with_retry(
        "Wikidata",
        lambda: _movie_candidates(item, session_wd, cache, show_cache, offset=next_offset, raw_results=raw_results_movie),
        interactive,
        progress,
    )
    if page is None:
        return None
    candidates = page.candidates
    raw_results_movie = page.raw_results
    next_offset = page.next_offset
    has_more = page.has_more
    selected = None
    while True:
        if not candidates:
            if interactive:
                console.print(f"No candidates found for {item.title}.")
                empty_choice = _prompt_choice("Choose: s (new search), m (manual), k (skip), q (quit): ", "k", progress)
                if empty_choice == "s":
                    query = _prompt_text("Search query", item.title, progress)
                    item = InferredItem(path=item.path, media_type=item.media_type, title=query, year=item.year)
                    raw_results_movie = None
                    next_offset = 0
                    page = _fetch_with_retry(
                        "Wikidata",
                        lambda: _movie_candidates(item, session_wd, cache, show_cache, offset=next_offset, raw_results=raw_results_movie),
                        interactive,
                        progress,
                    )
                    if page is None:
                        return None
                    candidates = page.candidates
                    raw_results_movie = page.raw_results
                    next_offset = page.next_offset
                    has_more = page.has_more
                    continue
                if empty_choice == "m":
                    selected = _prompt_manual_movie(item, progress)
                    break
                if empty_choice == "k":
                    return None
                if empty_choice == "q":
                    raise typer.Exit(code=0)
                console.print("Invalid choice.")
                continue
            selected = None
            break
        if candidates[0].confidence < min_confidence and interactive:
            console.print("Top confidence below minimum threshold.")
            low_choice = _prompt_choice(
                "Choose: s (new search), m (manual), k (skip), q (quit), Enter (review list): ",
                "",
                progress,
            )
            if low_choice in {"s", "m", "k", "q"}:
                if low_choice == "s":
                    query = _prompt_text("Search query", item.title, progress)
                    item = InferredItem(path=item.path, media_type=item.media_type, title=query, year=item.year)
                    raw_results_movie = None
                    next_offset = 0
                    page = _fetch_with_retry(
                        "Wikidata",
                        lambda: _movie_candidates(item, session_wd, cache, show_cache, offset=next_offset, raw_results=raw_results_movie),
                        interactive,
                        progress,
                    )
                    if page is None:
                        return None
                    candidates = page.candidates
                    raw_results_movie = page.raw_results
                    next_offset = page.next_offset
                    has_more = page.has_more
                    continue
                if low_choice == "m":
                    selected = _prompt_manual_movie(item, progress)
                    break
                if low_choice == "k":
                    return None
                if low_choice == "q":
                    raise typer.Exit(code=0)
        if yes and candidates[0].confidence >= min_confidence:
            selected = candidates[0]
            break
        if not interactive:
            selected = None
            break
        _maybe_enrich_candidates("movie", candidates, session_tv, session_wd, cache, interactive)
        choice = _select_candidate("movie", candidates, progress, has_more)
        if isinstance(choice, Candidate):
            selected = choice
            break
        if choice == "s":
            query = _prompt_text("Search query", item.title, progress)
            item = InferredItem(path=item.path, media_type=item.media_type, title=query, year=item.year)
            raw_results_movie = None
            next_offset = 0
            page = _fetch_with_retry(
                "Wikidata",
                lambda: _movie_candidates(item, session_wd, cache, show_cache, offset=next_offset, raw_results=raw_results_movie),
                interactive,
                progress,
            )
            if page is None:
                return None
            candidates = page.candidates
            raw_results_movie = page.raw_results
            next_offset = page.next_offset
            has_more = page.has_more
            continue
        if choice == "n":
            if not has_more:
                console.print("No more candidates.")
                continue
            page = _fetch_with_retry(
                "Wikidata",
                lambda: _movie_candidates(item, session_wd, cache, show_cache, offset=next_offset, raw_results=raw_results_movie),
                interactive,
                progress,
            )
            if page is None:
                return None
            candidates = page.candidates
            raw_results_movie = page.raw_results
            next_offset = page.next_offset
            has_more = page.has_more
            continue
        if choice == "m":
            selected = _prompt_manual_movie(item, progress)
            break
        if choice == "k":
            return None
        if choice == "q":
            raise typer.Exit(code=0)
    if not selected:
        return None
    _print_choice(selected)
    metadata = selected.metadata
    if metadata.get("manual"):
        cache.set_movie(item.title, {"qid": None, "title": metadata["title"], "year": metadata.get("year"), "manual": True})
    else:
        cache.set_movie(item.title, {"qid": metadata["qid"], "title": selected.title, "year": selected.year, "manual": False})
    cache.save()

    year = metadata.get("year") or selected.year
    if year is None and interactive:
        year_text = _prompt_text("Movie year (blank for Unknown Year)", "", progress)
        year = int(year_text) if year_text else None
    destination = plan_movie(library, metadata.get("title") or selected.title, year, item.path.suffix)
    plan = MovePlan(
        source=item.path,
        destination=unique_path(destination),
        mode=mode,
        media_type="movie",
        metadata={"title": metadata.get("title") or selected.title, "year": year},
    )
    _print_plan(plan)
    return plan


@app.command()
def organise(
    incoming: Path = typer.Option(..., exists=True, file_okay=False, dir_okay=True, help="Folder to scan"),
    library: Path = typer.Option(..., file_okay=False, dir_okay=True, help="Library root"),
    mode: str = typer.Option("dry-run", help="dry-run or apply"),
    move: bool = typer.Option(False, "--move", help="Move files (default behavior)", is_flag=True),
    copy: bool = typer.Option(False, "--copy", help="Copy instead of move", is_flag=True),
    extensions: str = typer.Option(DEFAULT_EXTENSIONS, help="Comma-separated extensions"),
    min_confidence: float = typer.Option(DEFAULT_MIN_CONFIDENCE, help="Minimum confidence for auto acceptance"),
    cache: Path = typer.Option(None, help="Cache path"),
    report: Path = typer.Option(None, help="Report path"),
    yes: bool = typer.Option(False, "--yes", help="Auto-accept top result when confidence >= 0.90", is_flag=True),
    limit: int = typer.Option(None, help="Limit number of files"),
    print_tree: bool = typer.Option(False, "--print-tree", help="Print planned destination tree", is_flag=True),
    interactive: bool = typer.Option(False, "--interactive", help="Force interactive mode", is_flag=True),
    no_interactive: bool = typer.Option(False, "--no-interactive", help="Disable interactive prompts", is_flag=True),
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

    cache_path = cache or library / ".plexify" / "cache.json"
    report_path = report or library / ".plexify" / "reports" / f"{now_timestamp()}.json"

    plans, errors = _plan_items(
        incoming=incoming,
        library=library,
        mode=mode,
        copy_mode=copy,
        interactive=interactive_mode,
        yes=yes,
        min_confidence=min_confidence,
        extensions=extensions,
        cache_path=cache_path,
        limit=limit,
        show_cache=interactive_mode or print_tree,
    )

    if print_tree and plans:
        tree = _build_tree([plan.destination for plan in plans])
        console.print(tree)

    apply_mode = mode == "apply"
    if apply_mode and plans:
        result = _apply_with_progress(plans, copy_mode=copy)
    else:
        result = execute_plans(plans, apply=apply_mode, copy_mode=copy)

    write_report(report_path, result.moved if apply_mode else plans, mode, copy)
    if result.errors or errors:
        console.print("Errors:")
        for error in result.errors + errors:
            console.print(f"- {error}")
        raise typer.Exit(code=1)
    if not plans:
        raise typer.Exit(code=1)
    raise typer.Exit(code=0)


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
    console.print("Nothing will be changed until you choose to apply the plan.")

    user_agent_missing = not os.getenv("PLEXIFY_USER_AGENT")
    if user_agent_missing:
        console.print("Note: Wikidata requests may be blocked without an informative User-Agent.")
        console.print("Set PLEXIFY_USER_AGENT to something like: plexify/0.1 (contact: you@example.com)")
        if not _confirm("Continue without setting it? [y/N]: ", False, None, show_default=False):
            raise typer.Exit(code=0)
    wikidata.search("Test")
    if not wikidata.is_available():
        console.print(
            "Network lookups appear to be unavailable. You can still organise files, but you may need to use manual search more often."
        )

    console.print("Where are the files you want to organise?")
    incoming_default = Path.cwd()
    while True:
        incoming_text = _prompt_text("Incoming folder", str(incoming_default), None)
        incoming = Path(incoming_text)
        if incoming.exists() and incoming.is_dir():
            break
        console.print("That path does not exist or is not a folder. Please try again.")

    exts = [ext.strip() for ext in DEFAULT_EXTENSIONS.split(",") if ext.strip()]
    found_files = iter_video_files(incoming, exts)
    console.print(f"Found {len(found_files)} video file(s) to review.")

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
        if any(library.iterdir()):
            console.print("Warning: this folder is not empty.")
            if not _confirm("Continue and place organised files here? [y/N]: ", False, None, show_default=False):
                continue
        break

    while True:
        incoming_resolved = incoming.resolve()
        library_resolved = library.resolve()
        if incoming_resolved == library_resolved:
            console.print("Incoming and library folders must be different.")
            library_text = _prompt_text("Library folder", str(library_default), None)
            library = Path(library_text)
            continue
        overlap = library_resolved.is_relative_to(incoming_resolved) or incoming_resolved.is_relative_to(library_resolved)
        if overlap:
            console.print("Warning: your incoming and library folders overlap.")
            console.print("This can cause Plexify to scan its own output on later runs.")
            console.print("Recommended: use separate folders (for example, C:\\Video\\Incoming and C:\\Video\\Library).")
            phrase = _prompt_text("To continue anyway, type: I UNDERSTAND\n> ", "", None, show_default=False)
            if phrase != "I UNDERSTAND":
                raise typer.Exit(code=0)
        break

    console.print("Next: choose how Plexify should behave.")
    use_defaults = _confirm("Use default settings? [Y/n]: ", True, None, show_default=False)

    extensions = DEFAULT_EXTENSIONS
    min_confidence = DEFAULT_MIN_CONFIDENCE
    limit: int | None = None
    interactive = True
    print_tree = True

    if not use_defaults:
        console.print("File extensions to include (comma-separated).")
        extensions = _prompt_text("Extensions", DEFAULT_EXTENSIONS, None)
        console.print("Minimum confidence for auto-accept when using --yes.")
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
        console.print("Optional: limit how many files are processed in this run.")
        while True:
            limit_text = _prompt_text("Limit", "none", None).strip().lower()
            if limit_text in {"none", ""}:
                limit = None
                break
            try:
                limit_value = int(limit_text)
            except ValueError:
                console.print("Enter a positive integer or leave blank.")
                continue
            if limit_value > 0:
                limit = limit_value
                break
            console.print("Enter a positive integer or leave blank.")
        console.print("Interactive mode lets you confirm matches and enter manual titles when needed.")
        interactive = _confirm("Interactive mode? [Y/n]: ", True, None, show_default=False)

    console.print("Plexify will now build a plan (dry run).")
    console.print("You will be asked to confirm matches when needed.")
    console.print("No files will be moved or copied during the dry run.")

    cache_path = library / ".plexify" / "cache.json"
    report_path = library / ".plexify" / "reports" / f"{now_timestamp()}.json"
    plans, errors = _plan_items(
        incoming=incoming,
        library=library,
        mode="dry-run",
        copy_mode=True,
        interactive=interactive,
        yes=False,
        min_confidence=min_confidence,
        extensions=extensions,
        cache_path=cache_path,
        limit=limit,
        show_cache=interactive or print_tree,
    )
    result = execute_plans(plans, apply=False, copy_mode=True)
    write_report(report_path, plans, "dry-run", True)

    console.print("Plan complete.")
    console.print(f"Planned items: {len(plans)}")
    console.print(f"Skipped: {len(result.skipped)}")
    console.print(f"Errors: {len(result.errors) + len(errors)}")
    if print_tree and plans:
        tree = _build_tree([plan.destination for plan in plans])
        console.print(tree)

    if not _confirm("Apply this plan now? [y/N]: ", False, None, show_default=False):
        console.print("No changes were made.")
        console.print("Next time, you can run:")
        console.print(
            _build_command(
                incoming,
                library,
                "dry-run",
                True,
                extensions,
                min_confidence,
                limit,
                interactive,
                print_tree,
            )
        )
        if user_agent_missing:
            console.print("Reminder: set PLEXIFY_USER_AGENT to include contact information.")
        raise typer.Exit(code=0)

    console.print("How should files be applied?")
    console.print("  1) Copy files (recommended)")
    console.print("  2) Move files")
    apply_choice = _prompt_choice("Choose [1]: ", "1", None, show_default=False)
    apply_copy = True
    if apply_choice == "2":
        console.print("Warning: move will remove the original files from the incoming folder.")
        phrase = _prompt_text("To proceed, type: MOVE MY FILES\n> ", "", None, show_default=False)
        if phrase != "MOVE MY FILES":
            console.print("Cancelled. No changes were made.")
            raise typer.Exit(code=0)
        apply_copy = False

    apply_report_path = library / ".plexify" / "reports" / f"{now_timestamp()}.json"
    apply_result = _apply_with_progress(plans, copy_mode=apply_copy)
    write_report(apply_report_path, apply_result.moved, "apply", apply_copy)

    console.print("Next time, you can run:")
    console.print(
        _build_command(
            incoming,
            library,
            "apply",
            apply_copy,
            extensions,
            min_confidence,
            limit,
            interactive,
            print_tree,
        )
    )
    if user_agent_missing:
        console.print("Reminder: set PLEXIFY_USER_AGENT to include contact information.")


if __name__ == "__main__":
    if len(sys.argv) == 1:
        wizard()
    else:
        app()
