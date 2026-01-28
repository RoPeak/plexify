import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

import requests
import typer
from rapidfuzz import fuzz
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
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


@dataclass
class Candidate:
    title: str
    year: Optional[int]
    source: str
    confidence: float
    metadata: dict[str, Any]


def _pause_progress(progress: Progress | None) -> bool:
    if progress is not None and getattr(progress, "live", None):
        progress.stop()
        return True
    return False


def _resume_progress(progress: Progress | None, was_running: bool) -> None:
    if progress is not None and was_running:
        progress.start()


def _prompt_text(prompt: str, default: str, progress: Progress | None) -> str:
    was_running = _pause_progress(progress)
    try:
        return Prompt.ask(prompt, default=default)
    finally:
        _resume_progress(progress, was_running)


def _prompt_choice(prompt: str, default: str, progress: Progress | None) -> str:
    return _prompt_text(prompt, default, progress).strip().lower()


def _confidence_score(title_guess: str, title_actual: str, year_guess: int | None, year_actual: int | None) -> float:
    base = fuzz.WRatio(title_guess, title_actual) / 100.0
    if year_guess and year_actual and year_guess == year_actual:
        base = min(1.0, base + 0.1)
    return max(0.0, min(1.0, base))


def _print_candidates(candidates: list[Candidate]) -> None:
    table = Table(title="Candidates")
    table.add_column("#")
    table.add_column("Title")
    table.add_column("Year")
    table.add_column("Source")
    table.add_column("Confidence")
    for idx, cand in enumerate(candidates, start=1):
        year_text = str(cand.year) if cand.year else "Unknown"
        table.add_row(str(idx), cand.title, year_text, cand.source, f"{cand.confidence:.2f}")
    console.print(table)


def _select_candidate(candidates: list[Candidate], progress: Progress | None) -> Candidate | None | str:
    if not candidates:
        return None
    while True:
        _print_candidates(candidates)
        choice = _prompt_choice(
            "Select: [Enter]=1, [1-9]=choose, s=search, m=manual, k=skip, q=quit >",
            "1",
            progress,
        )
        if choice == "1":
            return candidates[0]
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(candidates):
                return candidates[idx]
            console.print("Invalid selection.")
            continue
        if choice in {"s", "m", "k", "q"}:
            return choice
        console.print("Invalid choice.")


def _tv_candidates(item: InferredItem, session: requests.Session, cache: Cache) -> list[Candidate]:
    cached = cache.get_show(item.title)
    results: list[Candidate] = []
    if cached and not cached.get("manual"):
        show = tvmaze.TVMazeShow(id=int(cached["id"]), name=cached["name"], premiered=cached.get("premiered"))
        results.append(_tv_candidate_from_show(item, show, session))
        return results

    for show in tvmaze.search_shows(item.title, session=session)[:5]:
        results.append(_tv_candidate_from_show(item, show, session))
    return results


def _tv_candidate_from_show(item: InferredItem, show: tvmaze.TVMazeShow, session: requests.Session) -> Candidate:
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

    episode_title = None
    if item.season is not None and item.episode is not None:
        episodes = tvmaze.fetch_episodes(show.id, session=session)
        for ep in episodes:
            if ep.season == item.season and ep.number == item.episode:
                episode_title = ep.name
                break
        if episode_title:
            confidence = min(1.0, confidence + 0.1)
        metadata["episode_title"] = episode_title
    return Candidate(title=show.name, year=year, source="TVMaze", confidence=confidence, metadata=metadata)


def _movie_candidates(item: InferredItem, session: requests.Session, cache: Cache) -> list[Candidate]:
    cached = cache.get_movie(item.title)
    results: list[Candidate] = []
    if cached and not cached.get("manual"):
        film = wikidata.WikidataFilm(qid=cached["qid"], title=cached["title"], year=cached.get("year"), is_film=True)
        results.append(_movie_candidate_from_film(item, film))
        return results

    for cand in wikidata.search(item.title, session=session)[:5]:
        film = wikidata.fetch_entity(cand.qid, session=session)
        if not film.is_film:
            continue
        results.append(_movie_candidate_from_film(item, film))
    return results


def _movie_candidate_from_film(item: InferredItem, film: wikidata.WikidataFilm) -> Candidate:
    confidence = _confidence_score(item.title, film.title, item.year, film.year)
    metadata = {"qid": film.qid, "title": film.title, "year": film.year}
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
) -> MovePlan | None:
    console.print(f"Detected: {item.media_type.upper()} | Title guess: {item.title}")
    if item.media_type == "tv":
        console.print(f"Season/Episode guess: {item.season}/{item.episode}")
    if item.media_type == "tv":
        candidates = _fetch_with_retry(
            "TVMaze",
            lambda: _tv_candidates(item, session_tv, cache),
            interactive,
            progress,
        )
        if candidates is None:
            return None
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
                        candidates = _fetch_with_retry(
                            "TVMaze",
                            lambda: _tv_candidates(item, session_tv, cache),
                            interactive,
                            progress,
                        )
                        if candidates is None:
                            return None
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
                        candidates = _fetch_with_retry(
                            "TVMaze",
                            lambda: _tv_candidates(item, session_tv, cache),
                            interactive,
                            progress,
                        )
                        if candidates is None:
                            return None
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
            choice = _select_candidate(candidates, progress)
            if isinstance(choice, Candidate):
                selected = choice
                break
            if choice == "s":
                query = _prompt_text("Search query", item.title, progress)
                item = InferredItem(path=item.path, media_type=item.media_type, title=query, year=item.year, season=item.season, episode=item.episode)
                candidates = _fetch_with_retry(
                    "TVMaze",
                    lambda: _tv_candidates(item, session_tv, cache),
                    interactive,
                    progress,
                )
                if candidates is None:
                    return None
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

    candidates = _fetch_with_retry(
        "Wikidata",
        lambda: _movie_candidates(item, session_wd, cache),
        interactive,
        progress,
    )
    if candidates is None:
        return None
    selected = None
    while True:
        if not candidates:
            if interactive:
                console.print(f"No candidates found for {item.title}.")
                empty_choice = _prompt_choice("Choose: s (new search), m (manual), k (skip), q (quit): ", "k", progress)
                if empty_choice == "s":
                    query = _prompt_text("Search query", item.title, progress)
                    item = InferredItem(path=item.path, media_type=item.media_type, title=query, year=item.year)
                    candidates = _fetch_with_retry(
                        "Wikidata",
                        lambda: _movie_candidates(item, session_wd, cache),
                        interactive,
                        progress,
                    )
                    if candidates is None:
                        return None
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
                    candidates = _fetch_with_retry(
                        "Wikidata",
                        lambda: _movie_candidates(item, session_wd, cache),
                        interactive,
                        progress,
                    )
                    if candidates is None:
                        return None
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
        choice = _select_candidate(candidates, progress)
        if isinstance(choice, Candidate):
            selected = choice
            break
        if choice == "s":
            query = _prompt_text("Search query", item.title, progress)
            item = InferredItem(path=item.path, media_type=item.media_type, title=query, year=item.year)
            candidates = _fetch_with_retry(
                "Wikidata",
                lambda: _movie_candidates(item, session_wd, cache),
                interactive,
                progress,
            )
            if candidates is None:
                return None
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
    extensions: str = typer.Option(".mkv,.mp4,.avi,.m4v,.mov,.ts", help="Comma-separated extensions"),
    min_confidence: float = typer.Option(0.55, help="Minimum confidence for auto acceptance"),
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

    interactive_mode = True if interactive else not no_interactive

    cache_path = cache or library / ".plexify" / "cache.json"
    report_path = report or library / ".plexify" / "reports" / f"{now_timestamp()}.json"
    cache_store = Cache(cache_path)

    exts = [ext.strip() for ext in extensions.split(",") if ext.strip()]
    files = iter_video_files(incoming, exts)
    if limit:
        files = files[:limit]

    plans: list[MovePlan] = []
    errors: list[str] = []

    with Progress(SpinnerColumn(), TextColumn("{task.description}")) as progress:
        task = progress.add_task("Scanning files...", total=len(files))
        session_tv = tvmaze.create_session()
        session_wd = wikidata.create_session()
        total = len(files)
        for index, path in enumerate(files, start=1):
            was_running = _pause_progress(progress)
            console.print(f"File {index}/{total}: {path}")
            _resume_progress(progress, was_running)
            progress.advance(task)
            try:
                item = infer_item(path)
                plan = _process_item(
                    item=item,
                    library=library,
                    cache=cache_store,
                    mode=mode,
                    copy_mode=copy,
                    interactive=interactive_mode,
                    yes=yes,
                    min_confidence=min_confidence,
                    session_tv=session_tv,
                    session_wd=session_wd,
                    progress=progress,
                )
                if plan:
                    plans.append(plan)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{path}: {exc}")

    if print_tree and plans:
        tree = _build_tree([plan.destination for plan in plans])
        console.print(tree)

    apply_mode = mode == "apply"
    result: ExecutionResult = execute_plans(plans, apply=apply_mode, copy_mode=copy)

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


if __name__ == "__main__":
    app()
