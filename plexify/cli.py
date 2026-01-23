from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

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


def _select_candidate(candidates: list[Candidate]) -> Candidate | None | str:
    if not candidates:
        return None
    current = 0
    rejections = 0
    while True:
        _print_candidates(candidates)
        choice = console.input("Select [Enter=1, number, n, s, m, q]: ").strip().lower()
        if choice == "":
            return candidates[current]
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(candidates):
                return candidates[idx]
            console.print("Invalid selection.")
            continue
        if choice == "n":
            current = (current + 1) % len(candidates)
            rejections += 1
        elif choice in {"s", "m", "q"}:
            return choice
        else:
            console.print("Invalid choice.")
        if rejections >= 5:
            fallback = console.input("Couldn't confirm confidently. Choose: s (new search), m (manual), q (skip): ").strip().lower()
            if fallback in {"s", "m", "q"}:
                return fallback


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
    if show.premiered:
        year = int(show.premiered.split("-")[0])
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


def _prompt_manual_tv(item: InferredItem) -> Candidate:
    show_name = Prompt.ask("Show name", default=item.title)
    year_text = Prompt.ask("Show year", default=str(item.year) if item.year else "")
    season_text = Prompt.ask("Season", default=str(item.season) if item.season else "1")
    episode_text = Prompt.ask("Episode", default=str(item.episode) if item.episode else "1")
    episode_title = Prompt.ask("Episode title", default="")
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


def _prompt_manual_movie(item: InferredItem) -> Candidate:
    title = Prompt.ask("Movie title", default=item.title)
    year_text = Prompt.ask("Movie year", default=str(item.year) if item.year else "")
    year = int(year_text) if year_text else None
    metadata = {"qid": None, "title": title, "year": year, "manual": True}
    return Candidate(title=title, year=year, source="Manual", confidence=1.0, metadata=metadata)


def _print_plan(plan: MovePlan) -> None:
    console.print("PLAN")
    console.print(f"FROM: {plan.source}")
    console.print(f"TO:   {plan.destination}")


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
) -> MovePlan | None:
    console.print(f"Detected: {item.media_type.upper()} | Title guess: {item.title}")
    if item.media_type == "tv":
        console.print(f"Season/Episode guess: {item.season}/{item.episode}")
    if item.media_type == "tv":
        candidates = _tv_candidates(item, session_tv, cache)
        selected = None
        while True:
            if not candidates:
                selected = None
                break
            if candidates[0].confidence < min_confidence and interactive:
                console.print("Top confidence below minimum threshold.")
                low_choice = console.input("Choose: s (new search), m (manual), q (skip), Enter (review list): ").strip().lower()
                if low_choice in {"s", "m", "q"}:
                    if low_choice == "s":
                        query = Prompt.ask("Search query", default=item.title)
                        item = InferredItem(path=item.path, media_type=item.media_type, title=query, year=item.year, season=item.season, episode=item.episode)
                        candidates = _tv_candidates(item, session_tv, cache)
                        continue
                    if low_choice == "m":
                        selected = _prompt_manual_tv(item)
                        break
                    if low_choice == "q":
                        return None
            if yes and candidates[0].confidence >= 0.90:
                selected = candidates[0]
                break
            if not interactive:
                selected = None
                break
            choice = _select_candidate(candidates)
            if isinstance(choice, Candidate):
                selected = choice
                break
            if choice == "s":
                query = Prompt.ask("Search query", default=item.title)
                item = InferredItem(path=item.path, media_type=item.media_type, title=query, year=item.year, season=item.season, episode=item.episode)
                candidates = _tv_candidates(item, session_tv, cache)
                continue
            if choice == "m":
                selected = _prompt_manual_tv(item)
                break
            if choice == "q":
                return None
        if not selected:
            return None
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
            season = int(Prompt.ask("Season", default=str(item.season or 1)))
            episode = int(Prompt.ask("Episode", default=str(item.episode or 1)))
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

    candidates = _movie_candidates(item, session_wd, cache)
    selected = None
    while True:
        if not candidates:
            selected = None
            break
        if candidates[0].confidence < min_confidence and interactive:
            console.print("Top confidence below minimum threshold.")
            low_choice = console.input("Choose: s (new search), m (manual), q (skip), Enter (review list): ").strip().lower()
            if low_choice in {"s", "m", "q"}:
                if low_choice == "s":
                    query = Prompt.ask("Search query", default=item.title)
                    item = InferredItem(path=item.path, media_type=item.media_type, title=query, year=item.year)
                    candidates = _movie_candidates(item, session_wd, cache)
                    continue
                if low_choice == "m":
                    selected = _prompt_manual_movie(item)
                    break
                if low_choice == "q":
                    return None
        if yes and candidates[0].confidence >= 0.90:
            selected = candidates[0]
            break
        if not interactive:
            selected = None
            break
        choice = _select_candidate(candidates)
        if isinstance(choice, Candidate):
            selected = choice
            break
        if choice == "s":
            query = Prompt.ask("Search query", default=item.title)
            item = InferredItem(path=item.path, media_type=item.media_type, title=query, year=item.year)
            candidates = _movie_candidates(item, session_wd, cache)
            continue
        if choice == "m":
            selected = _prompt_manual_movie(item)
            break
        if choice == "q":
            return None
    if not selected:
        return None
    metadata = selected.metadata
    if metadata.get("manual"):
        cache.set_movie(item.title, {"qid": None, "title": metadata["title"], "year": metadata.get("year"), "manual": True})
    else:
        cache.set_movie(item.title, {"qid": metadata["qid"], "title": selected.title, "year": selected.year, "manual": False})
    cache.save()

    year = metadata.get("year") or selected.year
    if year is None and interactive:
        year_text = Prompt.ask("Movie year (blank for Unknown Year)", default="")
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
    move: bool = typer.Option(True, "--move/--copy", help="Move (default) or copy in apply mode"),
    extensions: str = typer.Option(".mkv,.mp4,.avi,.m4v,.mov,.ts", help="Comma-separated extensions"),
    min_confidence: float = typer.Option(0.55, help="Minimum confidence for auto acceptance"),
    cache: Path = typer.Option(None, help="Cache path"),
    report: Path = typer.Option(None, help="Report path"),
    yes: bool = typer.Option(False, help="Auto-accept top result when confidence >= 0.90"),
    limit: int = typer.Option(None, help="Limit number of files"),
    print_tree: bool = typer.Option(False, help="Print planned destination tree"),
    interactive: bool = typer.Option(True, "--interactive/--no-interactive", help="Interactive mode"),
) -> None:
    if mode not in {"dry-run", "apply"}:
        console.print("Invalid mode. Use dry-run or apply.")
        raise typer.Exit(code=2)

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
        for path in files:
            progress.advance(task)
            try:
                item = infer_item(path)
                plan = _process_item(
                    item=item,
                    library=library,
                    cache=cache_store,
                    mode=mode,
                    copy_mode=not move,
                    interactive=interactive,
                    yes=yes,
                    min_confidence=min_confidence,
                    session_tv=session_tv,
                    session_wd=session_wd,
                )
                if plan:
                    plans.append(plan)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{path}: {exc}")

    if print_tree and plans:
        tree = _build_tree([plan.destination for plan in plans])
        console.print(tree)

    apply_mode = mode == "apply"
    result: ExecutionResult = execute_plans(plans, apply=apply_mode, copy_mode=not move)

    write_report(report_path, result.moved if apply_mode else plans, mode, not move)
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
