from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from . import music as music_util
from .cache import Cache
from .cache_policy import cache_entry_compatible, cache_entry_confirmed_or_auto, reusable_cache_safe, year_distance
from .commands import music_flow, plan_flow, video_flow
from .executor import execute_plans
from .infer import InferredItem
from .report import open_report_stream
from .services import movie_matcher, music_matcher, selection_policy, tv_matcher
from .sources import musicbrainz, tvmaze, wikidata
from .ui import format_path
from .util import (
    ExecutionResult,
    MovePlan,
    make_search_query,
    movie_cache_key,
    tv_episode_cache_key,
    tv_show_cache_key,
    tv_show_folder_cache_key,
    unique_path,
    unique_plan_path,
)

AUTO_ACCEPT_GAP = 0.08
TV_SEASON_TOKEN_RE = r"(?:season|series|seaon|seson|seasn)"
TV_EXPLICIT_SEASON_RE = re.compile(rf"(?<![A-Za-z0-9]){TV_SEASON_TOKEN_RE}[-_. ]*(\d{{1,2}})(?![A-Za-z0-9])", re.IGNORECASE)
TV_SXXEYY_CAPTURE_RE = re.compile(r"\bs(\d{1,2})e\d{1,3}\b", re.IGNORECASE)
TV_XYY_CAPTURE_RE = re.compile(r"\b(\d{1,2})x\d{1,3}\b", re.IGNORECASE)
MUSIC_FEAT_SUFFIX_RE = re.compile(r"\s+(?:feat\.?|ft\.?|featuring)\s+.*$", re.IGNORECASE)


@dataclass
class UICandidate:
    title: str
    year: int | None
    source: str
    confidence: float
    metadata: dict[str, Any]
    enrichment: dict[str, Any] | None = None


@dataclass
class UICandidatePage:
    candidates: list[UICandidate]
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


@dataclass(frozen=True)
class MusicPlannedTrack:
    source: Path
    track_number: int
    track_number_text: str
    track_title: str
    track_artist: str
    ext: str
    disc_number: int | None = None


def build_search_query(title: str, hint: str | None) -> str:
    return plan_flow.build_search_query(title, hint)


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


def switch_item_media_type(item: InferredItem, target_media_type: str) -> InferredItem:
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
            episode_end=item.episode_end,
            episode_title=item.episode_title,
        )
    return InferredItem(
        path=item.path,
        media_type="movie",
        title=item.title,
        year=item.year,
        season=None,
        episode=None,
        episode_end=None,
        episode_title=None,
    )


def resolve_media_type_override(
    item: InferredItem,
    cache: Cache,
    incoming_root: Path | None,
    media_type_overrides: dict[str, str] | None,
) -> tuple[InferredItem, str | None]:
    return plan_flow.resolve_media_type_override(
        item=item,
        incoming_root=incoming_root,
        cache=cache,
        media_type_overrides=media_type_overrides,
        media_override_key=_media_override_key,
        switch_item_media_type=switch_item_media_type,
    )


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


def apply_tv_folder_season_lock(item: InferredItem, cache: Cache, folder_show_key: str | None) -> InferredItem:
    if folder_show_key is None or item.media_type != "tv":
        return item
    cached = cache.get_show(folder_show_key)
    if not selection_policy.folder_show_cache_entry_is_trusted(cached):
        return item
    assert isinstance(cached, dict)
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
        episode_end=item.episode_end,
        episode_title=item.episode_title,
    )


def with_title(item: InferredItem, title: str) -> InferredItem:
    return InferredItem(
        path=item.path,
        media_type=item.media_type,
        title=title,
        year=item.year,
        season=item.season,
        episode=item.episode,
        episode_end=item.episode_end,
        episode_title=item.episode_title,
    )


def auto_acceptable(
    candidates: list[UICandidate],
    min_confidence: float,
    *,
    title: str,
    search_query: str,
    target_year: int | None,
) -> bool:
    if not candidates:
        return False
    second_conf = candidates[1].confidence if len(candidates) > 1 else None
    return movie_matcher.auto_acceptable(
        top_confidence=candidates[0].confidence,
        second_confidence=second_conf,
        top_year=candidates[0].year,
        min_confidence=min_confidence,
        title=title,
        search_query=search_query,
        target_year=target_year,
        min_gap=AUTO_ACCEPT_GAP,
    )


def resolve_destination(
    destination: Path,
    on_conflict: str,
    planned: dict[str, int] | None,
    *,
    platform: str = "auto",
) -> tuple[Path | None, bool]:
    def _path_exists_safe(path: Path) -> bool:
        try:
            return path.exists()
        except OSError:
            return False

    changed = False
    if _path_exists_safe(destination):
        if on_conflict == "skip":
            return None, False
        if on_conflict == "rename":
            destination = unique_path(destination)
            changed = True
    if planned is None:
        planned = {}
    destination, planned_changed = unique_plan_path(destination, planned, platform=platform)
    return destination, changed or planned_changed


def _tv_search_cache_key(query: str, year: int | None) -> str:
    year_text = str(year) if year is not None else "unknown"
    return f"{query.strip().casefold()}|{year_text}"


def _normalize_tv_retry_query(value: str) -> str:
    return tv_matcher.normalize_tv_retry_query(value, TV_EXPLICIT_SEASON_RE)


def _build_tv_fallback_queries(title: str, hint: str | None, year: int | None = None) -> list[str]:
    return plan_flow.build_tv_fallback_queries(title, hint, year)


def _tv_confidence_score(title_guess: str, title_actual: str, year_guess: int | None, year_actual: int | None) -> float:
    return tv_matcher.tv_confidence_score(title_guess, title_actual, year_guess, year_actual)


def _confidence_score(title_guess: str, title_actual: str, year_guess: int | None, year_actual: int | None) -> float:
    return movie_matcher.confidence_score(title_guess, title_actual, year_guess, year_actual)


def _tv_candidate_from_show(item: InferredItem, show: tvmaze.TVMazeShow) -> UICandidate:
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
    return UICandidate(title=show.name, year=year, source="TVMaze", confidence=confidence, metadata=metadata)


def _movie_candidate_from_film(
    item: InferredItem,
    film: wikidata.WikidataFilm,
    *,
    description: str | None = None,
) -> UICandidate:
    confidence = _confidence_score(item.title, film.title, item.year, film.year)
    metadata = {"qid": film.qid, "title": film.title, "year": film.year, "description": description}
    return UICandidate(title=film.title, year=film.year, source="Wikidata", confidence=confidence, metadata=metadata)


def _reusable_movie_cache_safe(item: InferredItem) -> bool:
    return reusable_cache_safe(item.title, item.year)


def _reusable_tv_cache_safe(item: InferredItem) -> bool:
    return reusable_cache_safe(item.title, item.year)


def load_tv_candidates(
    *,
    item: InferredItem,
    session: requests.Session,
    cache: Cache,
    incoming_root: Path,
    cache_key: str,
    offset: int,
    raw_results: list[tvmaze.TVMazeShow] | None,
    search_query: str,
    offline: bool,
    search_cache: dict[str, list[tvmaze.TVMazeShow]] | None,
) -> UICandidatePage:
    return video_flow.tv_candidates(
        item=item,
        session=session,
        cache=cache,
        show_cache=False,
        incoming_root=incoming_root,
        cache_key=cache_key,
        offset=offset,
        raw_results=raw_results,
        search_query=search_query,
        progress=None,
        limit=5,
        offline=offline,
        interactive=False,
        search_cache=search_cache,
        reusable_tv_cache_safe_fn=_reusable_tv_cache_safe,
        tv_show_cache_key_fn=tv_show_cache_key,
        tv_episode_cache_key_fn=tv_episode_cache_key,
        tv_show_folder_cache_key_fn=tv_show_folder_cache_key,
        cache_entry_confirmed_or_auto_fn=cache_entry_confirmed_or_auto,
        cache_entry_compatible_fn=cache_entry_compatible,
        log_event_fn=lambda *_args, **_kwargs: None,
        logger=None,
        safe_print_fn=lambda *_args, **_kwargs: None,
        rich_escape_fn=str,
        candidate_cls=UICandidate,
        candidate_page_cls=UICandidatePage,
        tv_candidate_from_show_fn=_tv_candidate_from_show,
        make_search_query_fn=make_search_query,
        tv_search_cache_key_fn=_tv_search_cache_key,
        normalize_tv_retry_query_fn=_normalize_tv_retry_query,
        build_tv_fallback_queries_fn=_build_tv_fallback_queries,
        year_distance_fn=year_distance,
    )


def load_movie_candidates(
    *,
    item: InferredItem,
    session: requests.Session,
    cache: Cache,
    cache_key: str,
    offset: int,
    raw_results: list[wikidata.WikidataCandidate] | None,
    search_query: str,
    offline: bool,
    movie_entity_cache: dict[str, wikidata.WikidataFilm] | None,
) -> UICandidatePage:
    return video_flow.movie_candidates(
        item=item,
        session=session,
        cache=cache,
        show_cache=False,
        cache_key=cache_key,
        offset=offset,
        raw_results=raw_results,
        search_query=search_query,
        progress=None,
        limit=5,
        offline=offline,
        interactive=False,
        movie_entity_cache=movie_entity_cache,
        movie_cache_key_fn=movie_cache_key,
        reusable_movie_cache_safe_fn=_reusable_movie_cache_safe,
        cache_entry_confirmed_or_auto_fn=cache_entry_confirmed_or_auto,
        cache_entry_compatible_fn=cache_entry_compatible,
        log_event_fn=lambda *_args, **_kwargs: None,
        logger=None,
        safe_print_fn=lambda *_args, **_kwargs: None,
        rich_escape_fn=str,
        movie_candidate_from_film_fn=_movie_candidate_from_film,
        candidate_page_cls=UICandidatePage,
        build_movie_fallback_queries_fn=plan_flow.build_movie_fallback_queries,
        make_search_query_fn=make_search_query,
        year_distance_fn=year_distance,
    )


def apply_with_streamed_report(
    plans: list[MovePlan],
    *,
    copy_mode: bool,
    on_conflict: str,
    report_path: Path,
    progress_callback=None,
    cancel_callback=None,
    copy_workers: int = 1,
) -> ExecutionResult:
    total = len(plans)
    if progress_callback is not None:
        progress_callback(
            {
                "phase": "starting",
                "completed": 0,
                "total": total,
                "report_path": report_path,
                "message": f"Opening organise report at {report_path}",
            }
        )
    stream = open_report_stream(report_path, mode="apply", copy_mode=copy_mode)
    try:
        if progress_callback is not None:
            progress_callback(
                {
                    "phase": "report-opened",
                    "completed": 0,
                    "total": total,
                    "report_path": report_path,
                    "message": "Organise report opened. Starting file operations.",
                }
            )
        result = execute_plans(
            plans,
            apply=True,
            copy_mode=copy_mode,
            on_conflict=on_conflict,
            on_applied=stream.append,
            on_plan_event=(
                lambda payload: progress_callback({**payload, "report_path": report_path})
                if progress_callback is not None
                else None
            ),
            cancel_callback=cancel_callback,
            copy_workers=copy_workers,
        )
        completed_count = len(result.moved) + len(result.skipped) + len(result.errors)
        if progress_callback is not None:
            progress_callback(
                {
                    "phase": "finalizing-report",
                    "completed": completed_count,
                    "total": total,
                    "report_path": report_path,
                    "message": "Finalizing organise report.",
                }
            )
        stream.finalize()
        if progress_callback is not None:
            progress_callback(
                {
                    "phase": "done",
                    "completed": completed_count,
                    "total": total,
                    "report_path": report_path,
                    "message": f"Organisation file operations finished ({completed_count} of {total}).",
                }
            )
        return result
    finally:
        stream.close()


def rank_music_candidates(
    candidates: list[musicbrainz.ReleaseCandidate],
    track_count: int,
    requested_title: str,
    requested_year: int | None,
) -> list[musicbrainz.ReleaseCandidate]:
    return music_matcher.rank_music_candidates(candidates, track_count, requested_title, requested_year)


def music_tracks_from_filenames(
    tracks: list[music_util.TrackInfo],
    *,
    disc_number: int | None = None,
    multi_disc: bool = False,
) -> list[MusicPlannedTrack]:
    planned: list[MusicPlannedTrack] = []
    for track in tracks:
        if track.track_number >= 100:
            inferred_disc_number = track.track_number // 100
            track_number_text = music_util.format_track_number(track.track_number, multi_disc=True)
            planned_track_number = track.track_number
        elif multi_disc and disc_number is not None and disc_number > 0:
            inferred_disc_number = disc_number
            planned_track_number = disc_number * 100 + track.track_number
            track_number_text = music_util.format_track_number(track.track_number, disc_number=disc_number, multi_disc=True)
        else:
            inferred_disc_number = None
            planned_track_number = track.track_number
            track_number_text = music_util.format_track_number(track.track_number, multi_disc=False)
        planned.append(
            MusicPlannedTrack(
                source=track.source,
                track_number=planned_track_number,
                track_number_text=track_number_text,
                track_title=track.track_title,
                track_artist=track.track_artist,
                ext=track.ext,
                disc_number=inferred_disc_number,
            )
        )
    return planned


def map_musicbrainz_tracks(
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
        mapped.append(
            MusicPlannedTrack(
                source=source_track.source,
                track_number=mb_track.number,
                track_number_text=music_util.format_track_number(mb_track.number),
                track_title=mb_track.title,
                track_artist=source_track.track_artist,
                ext=source_track.ext,
                disc_number=mb_track.disc,
            )
        )
    return mapped, None


def map_musicbrainz_by_order(
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


def _primary_artist_name(value: str) -> str:
    cleaned = MUSIC_FEAT_SUFFIX_RE.sub("", value or "")
    return " ".join(cleaned.split()).strip()


def normalise_artist_key(value: str | None) -> str:
    return _primary_artist_name(value or "").casefold()


def _dominant_track_artist_ratio(album: music_util.AlbumGroup) -> float:
    counts: Counter[str] = Counter()
    total = 0
    for track in album.tracks:
        key = normalise_artist_key(track.track_artist)
        if not key:
            continue
        total += 1
        counts[key] += 1
    if not total or not counts:
        return 0.0
    return counts.most_common(1)[0][1] / total


def should_use_various_artists(album: music_util.AlbumGroup, candidate_artist: str | None) -> bool:
    candidate_key = normalise_artist_key(candidate_artist)
    if candidate_key in {"various artists", "va", "various"}:
        return True
    album_key = normalise_artist_key(album.artist)
    if album_key in {"various artists", "va", "various"}:
        return _dominant_track_artist_ratio(album) < 0.8
    if candidate_key or album_key:
        return False
    return _dominant_track_artist_ratio(album) < 0.8


def normalise_music_decision_entry(entry: dict[str, Any] | None) -> dict[str, Any] | None:
    return music_flow.normalise_music_decision_entry(entry)


def describe_cache_context(*, cache_hit: bool, cache_reusable: bool, auto_selectable: bool) -> str:
    if cache_hit and cache_reusable:
        return "reusable cache"
    if cache_hit:
        return "cache hit"
    if auto_selectable:
        return "auto-selectable"
    return "search result"


def describe_music_verification_warning(*, verify: bool, offline: bool, available: bool) -> str | None:
    if not verify:
        return "Verification disabled; filename fallback will be used unless you choose otherwise."
    if offline:
        return "Offline mode enabled; MusicBrainz verification is unavailable."
    if not available:
        return "MusicBrainz is unavailable; choose a fallback or skip the album."
    return None


def describe_apply_result(
    *,
    result: ExecutionResult,
    report_path: Path,
    apply_report_path: Path | None,
) -> list[str]:
    lines = [
        f"Moved/copied: {len(result.moved)}",
        f"Skipped: {len(result.skipped)}",
        f"Errors: {len(result.errors)}",
        f"Report path: {format_path(report_path)}",
    ]
    if apply_report_path is not None:
        lines.append(f"Apply report path: {format_path(apply_report_path)}")
    return lines
