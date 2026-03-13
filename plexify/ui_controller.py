from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import requests

from . import music as music_util
from .cache import Cache, NullCache
from .executor import execute_plans
from .infer import InferredItem, infer_item
from .planner import plan_movie, plan_tv_show
from .report import write_report
from .sources import musicbrainz, tvmaze, wikidata
from .tv_episode_cache import EpisodeCache
from .ui import format_path
from .util import ExecutionResult, MovePlan, build_cache_key, iter_video_files, now_timestamp, tv_show_folder_cache_key


def _cli() -> Any:
    from . import cli

    return cli


@dataclass(frozen=True)
class UICandidateState:
    title: str
    year: int | None
    source: str
    confidence: float
    summary: str


@dataclass(frozen=True)
class VideoUIConfig:
    incoming: Path
    library: Path
    mode: str = "dry-run"
    copy_mode: bool = True
    extensions: str = ".mkv,.mp4,.avi,.m4v,.mov,.ts"
    min_confidence: float = 0.90
    use_cache: bool = True
    offline: bool = False
    media_type: str = "auto"
    on_conflict: str = "rename"


@dataclass
class VideoReviewItem:
    item: InferredItem
    search_query: str
    cache_key: str
    candidates: list[Any] = field(default_factory=list)
    candidate_states: list[UICandidateState] = field(default_factory=list)
    selected_candidate_index: int | None = None
    manual_candidate: Any | None = None
    skipped: bool = False
    auto_selectable: bool = False
    cache_hit: bool = False
    cache_reusable: bool = False
    has_more: bool = False
    next_offset: int = 0
    raw_results: list[Any] | None = None
    warning: str | None = None

    @property
    def resolved(self) -> bool:
        return self.skipped or self.manual_candidate is not None or self.selected_candidate_index is not None


@dataclass(frozen=True)
class MusicCandidateState:
    artist: str
    title: str
    year: int | None
    score: float
    mbid: str


@dataclass(frozen=True)
class MusicUIConfig:
    source: Path
    library: Path
    mode: str = "dry-run"
    copy_mode: bool = True
    extensions: str = "flac,mp3,m4a"
    verify: bool = True
    keep_art: bool = True
    keep_cue: bool = False
    keep_log: bool = False
    offline: bool = False
    cleanup_empty_dirs: bool = False
    cleanup_unknown_files: bool = False
    mismatch_policy: str = "ask"


@dataclass
class MusicReviewAlbum:
    album: music_util.AlbumGroup
    candidates: list[musicbrainz.ReleaseCandidate] = field(default_factory=list)
    candidate_states: list[MusicCandidateState] = field(default_factory=list)
    selected_candidate_index: int | None = None
    decision: str | None = None
    cached_decision: str | None = None
    warning: str | None = None

    @property
    def resolved(self) -> bool:
        return self.decision is not None


@dataclass(frozen=True)
class PreviewState:
    plans: list[MovePlan]
    warnings: list[str]
    summary_lines: list[str]


@dataclass(frozen=True)
class ApplyResultState:
    result: ExecutionResult
    report_path: Path
    apply_report_path: Path | None
    summary_lines: list[str]


class VideoUIController:
    def __init__(self, config: VideoUIConfig) -> None:
        self.config = config
        self.items: list[VideoReviewItem] = []
        self.errors: list[str] = []
        self.stats: dict[str, int] = {"cache_hits": 0}
        self._episode_cache = EpisodeCache()
        self._cache = Cache(config.library / ".plexify" / "cache.json") if config.use_cache else NullCache()
        self._media_type_overrides: dict[str, str] = {}
        self._tv_search_cache: dict[str, list[tvmaze.TVMazeShow]] = {}
        self._movie_entity_cache: dict[str, wikidata.WikidataFilm] = {}

    def scan(self) -> None:
        cli = _cli()
        self.items = []
        self.errors = []
        exts = cli._parse_extensions(self.config.extensions)
        for path in iter_video_files(self.config.incoming, exts):
            item = infer_item(path)
            item, _override_key = cli._resolve_media_type_override(
                item,
                self._cache,
                self.config.incoming,
                self._media_type_overrides,
            )
            folder_show_key = tv_show_folder_cache_key(item.path, self.config.incoming) if item.media_type == "tv" else None
            item = cli._apply_tv_folder_season_lock(item, self._cache, folder_show_key)
            if self.config.media_type != "auto" and item.media_type != self.config.media_type:
                continue
            search_query = cli._build_search_query(item.title, None)
            cache_key = build_cache_key(item.path, self.config.incoming, item.media_type, item.year)
            state = VideoReviewItem(
                item=item,
                search_query=search_query,
                cache_key=cache_key,
            )
            self._load_video_candidates(state)
            self.items.append(state)

    def _load_video_candidates(self, state: VideoReviewItem) -> None:
        cli = _cli()
        item = state.item
        try:
            if item.media_type == "tv":
                with tvmaze.create_session() as session_tv:
                    page = cli._tv_candidates(
                        item,
                        session_tv,
                        self._cache,
                        show_cache=False,
                        incoming_root=self.config.incoming,
                        cache_key=state.cache_key,
                        offset=state.next_offset,
                        raw_results=state.raw_results,
                        search_query=state.search_query,
                        progress=None,
                        offline=self.config.offline,
                        interactive=False,
                        search_cache=self._tv_search_cache,
                    )
                    if page.candidates:
                        cli._maybe_fetch_episode_title(
                            item,
                            page.candidates[0],
                            session_tv,
                            self._episode_cache,
                            bump_confidence=True,
                        )
            else:
                with wikidata.create_session() as session_wd:
                    page = cli._movie_candidates(
                        item,
                        session_wd,
                        self._cache,
                        show_cache=False,
                        cache_key=state.cache_key,
                        offset=state.next_offset,
                        raw_results=state.raw_results,
                        search_query=state.search_query,
                        progress=None,
                        offline=self.config.offline,
                        interactive=False,
                        movie_entity_cache=self._movie_entity_cache,
                    )
        except requests.RequestException as exc:
            state.warning = f"{item.path.name}: {exc.__class__.__name__}"
            self.errors.append(state.warning)
            state.candidates = []
            state.candidate_states = []
            return

        if page.cache_hit:
            self.stats["cache_hits"] += 1
        state.candidates = list(page.candidates)
        state.candidate_states = [
            UICandidateState(
                title=candidate.title,
                year=candidate.year,
                source=candidate.source,
                confidence=candidate.confidence,
                summary=f"{candidate.title} ({candidate.year or 'Unknown'}) [{candidate.confidence:.2f}]",
            )
            for candidate in page.candidates
        ]
        state.cache_hit = page.cache_hit
        state.cache_reusable = page.cache_reusable
        state.has_more = page.has_more
        state.next_offset = page.next_offset
        state.raw_results = page.raw_results
        state.auto_selectable = bool(
            page.candidates
            and cli._auto_acceptable(
                page.candidates,
                self.config.min_confidence,
                title=state.item.title,
                search_query=state.search_query,
                target_year=state.item.year,
            )
        )

    def accept_candidate(self, index: int, candidate_index: int = 0) -> None:
        state = self.items[index]
        if not state.candidates:
            return
        state.selected_candidate_index = max(0, min(candidate_index, len(state.candidates) - 1))
        state.manual_candidate = None
        state.skipped = False

    def skip_item(self, index: int) -> None:
        state = self.items[index]
        state.skipped = True
        state.selected_candidate_index = None
        state.manual_candidate = None

    def manual_select(
        self,
        index: int,
        *,
        title: str,
        year: int | None = None,
        season: int | None = None,
        episode: int | None = None,
        episode_title: str | None = None,
    ) -> None:
        cli = _cli()
        state = self.items[index]
        metadata: dict[str, Any]
        if state.item.media_type == "tv":
            metadata = {
                "id": None,
                "name": title,
                "year": year,
                "season": season if season is not None else state.item.season,
                "episode": episode if episode is not None else state.item.episode,
                "episode_title": episode_title or state.item.episode_title,
                "manual": True,
            }
        else:
            metadata = {"qid": None, "title": title, "year": year, "manual": True}
        state.manual_candidate = cli.Candidate(
            title=title,
            year=year,
            source="Manual",
            confidence=1.0,
            metadata=metadata,
        )
        state.selected_candidate_index = None
        state.skipped = False

    def refine_search(self, index: int, query: str) -> None:
        cli = _cli()
        state = self.items[index]
        if not query.strip():
            return
        state.item = cli._with_title(state.item, query.strip())
        state.search_query = cli._build_search_query(query.strip(), None)
        state.next_offset = 0
        state.raw_results = None
        state.selected_candidate_index = None
        state.manual_candidate = None
        self._load_video_candidates(state)

    def switch_media_type(self, index: int, media_type: str) -> None:
        cli = _cli()
        state = self.items[index]
        state.item = cli._switch_item_media_type(state.item, media_type)
        state.cache_key = build_cache_key(state.item.path, self.config.incoming, state.item.media_type, state.item.year)
        state.search_query = cli._build_search_query(state.item.title, None)
        state.next_offset = 0
        state.raw_results = None
        state.selected_candidate_index = None
        state.manual_candidate = None
        state.skipped = False
        self._load_video_candidates(state)

    def next_page(self, index: int) -> None:
        state = self.items[index]
        if not state.has_more:
            return
        self._load_video_candidates(state)

    def apply_choice_to_folder(self, index: int) -> None:
        state = self.items[index]
        if state.selected_candidate_index is None or not state.candidates:
            return
        parent = state.item.path.parent
        selected_index = state.selected_candidate_index
        for other in self.items:
            if other.item.path.parent == parent and other.item.media_type == state.item.media_type and other.candidates:
                other.selected_candidate_index = min(selected_index, len(other.candidates) - 1)
                other.manual_candidate = None
                other.skipped = False

    def build_preview(self) -> PreviewState:
        cli = _cli()
        plans: list[MovePlan] = []
        warnings: list[str] = []
        planned: dict[str, int] = {}
        skipped = 0
        for state in self.items:
            if state.skipped or not state.resolved:
                skipped += 1
                continue
            selected = state.manual_candidate
            if selected is None and state.selected_candidate_index is not None and state.candidates:
                selected = state.candidates[state.selected_candidate_index]
            if selected is None:
                skipped += 1
                continue
            if state.item.media_type == "movie":
                year = selected.metadata.get("year") or selected.year or state.item.year
                title = selected.metadata.get("title") or selected.title
                destination = plan_movie(self.config.library, title, year, state.item.path.suffix)
                metadata = {"title": title, "year": year}
            else:
                show_name = selected.metadata.get("name") or selected.title
                show_year = selected.metadata.get("year") or selected.year or state.item.year
                season = selected.metadata.get("season") if selected.metadata.get("manual") else state.item.season
                episode = selected.metadata.get("episode") if selected.metadata.get("manual") else state.item.episode
                episode_title = selected.metadata.get("episode_title") or state.item.episode_title
                if season is None or episode is None:
                    warnings.append(f"Skipped {state.item.path.name}: missing season or episode.")
                    skipped += 1
                    continue
                destination = plan_tv_show(
                    self.config.library,
                    show_name,
                    show_year,
                    int(season),
                    int(episode),
                    None,
                    episode_title,
                    state.item.path.suffix,
                )
                metadata = {
                    "show": show_name,
                    "year": show_year,
                    "season": int(season),
                    "episode": int(episode),
                    "episode_title": episode_title,
                }
            destination, _collision = cli._resolve_destination(destination, self.config.on_conflict, planned, None)
            if destination is None:
                warnings.append(f"Skipped {state.item.path.name}: conflict policy skipped destination.")
                skipped += 1
                continue
            if len(str(destination)) > 240:
                warnings.append(f"Long destination path: {format_path(destination)}")
            plans.append(
                MovePlan(
                    source=state.item.path,
                    destination=destination,
                    mode=self.config.mode,
                    media_type=state.item.media_type,
                    metadata=metadata,
                )
            )
        summary_lines = [
            f"Items: {len(self.items)}",
            f"Planned: {len(plans)}",
            f"Skipped/unresolved: {skipped}",
            f"Cache hits: {self.stats['cache_hits']}",
        ]
        return PreviewState(plans=plans, warnings=warnings, summary_lines=summary_lines)

    def apply_preview(self, preview: PreviewState) -> ApplyResultState:
        cli = _cli()
        report_path = self.config.library / ".plexify" / "reports" / f"{now_timestamp()}.json"
        apply_report_path: Path | None = None
        if self.config.mode == "apply" and preview.plans:
            apply_report_path = report_path
            result = cli._apply_with_streamed_report(
                preview.plans,
                copy_mode=self.config.copy_mode,
                on_conflict=self.config.on_conflict,
                report_path=report_path,
            )
        else:
            result = execute_plans(
                preview.plans,
                apply=self.config.mode == "apply",
                copy_mode=self.config.copy_mode,
                on_conflict=self.config.on_conflict,
            )
            write_report(report_path, preview.plans if self.config.mode == "dry-run" or not preview.plans else [], self.config.mode, self.config.copy_mode)
        summary_lines = [
            f"Moved/copied: {len(result.moved)}",
            f"Skipped: {len(result.skipped)}",
            f"Errors: {len(result.errors)}",
            f"Report path: {format_path(report_path)}",
        ]
        return ApplyResultState(
            result=result,
            report_path=report_path,
            apply_report_path=apply_report_path,
            summary_lines=summary_lines,
        )


class MusicUIController:
    def __init__(self, config: MusicUIConfig) -> None:
        self.config = config
        self.albums: list[MusicReviewAlbum] = []
        self.errors: list[str] = []

    def scan(self) -> None:
        cli = _cli()
        self.albums = []
        self.errors = []
        albums, errors = music_util.discover_albums(self.config.source, cli._parse_extensions(self.config.extensions))
        self.errors.extend(errors)
        verify = self.config.verify and not self.config.offline and musicbrainz.is_available()
        session = musicbrainz.create_session() if verify else None
        try:
            for album in albums:
                entry = MusicReviewAlbum(album=album)
                cache = Cache(self.config.library / ".plexify" / "cache.json")
                cached = cli._normalise_music_decision_entry(cache.get_music(music_util.album_decision_cache_key(album)))
                if cached is not None:
                    entry.cached_decision = str(cached.get("decision") or "")
                if verify and session is not None:
                    candidates = musicbrainz.search_releases(
                        album.artist,
                        album.album,
                        limit=8,
                        session=session,
                        year=album.year,
                    )
                    ranked = cli._rank_music_candidates(candidates, len(album.tracks), album.album, album.year)
                    entry.candidates = ranked
                    entry.candidate_states = [
                        MusicCandidateState(
                            artist=candidate.artist,
                            title=candidate.title,
                            year=candidate.year,
                            score=candidate.score,
                            mbid=candidate.mbid,
                        )
                        for candidate in ranked
                    ]
                if entry.cached_decision == "skip_album":
                    entry.decision = "skip_album"
                self.albums.append(entry)
        finally:
            if session is not None:
                session.close()

    def select_candidate(self, index: int, candidate_index: int = 0) -> None:
        album = self.albums[index]
        if not album.candidates:
            return
        album.selected_candidate_index = max(0, min(candidate_index, len(album.candidates) - 1))
        album.decision = "selected"

    def choose_filename_fallback(self, index: int) -> None:
        self.albums[index].decision = "filename_fallback"

    def choose_filename_titles_fallback(self, index: int) -> None:
        self.albums[index].decision = "filename_titles_fallback"

    def choose_order_fallback(self, index: int) -> None:
        self.albums[index].decision = "order_fallback"

    def skip_album(self, index: int) -> None:
        self.albums[index].decision = "skip_album"

    def skip_remaining(self, index: int) -> None:
        for album in self.albums[index:]:
            if album.decision is None:
                album.decision = "skip_album"

    def build_preview(self) -> PreviewState:
        cli = _cli()
        plans: list[MovePlan] = []
        warnings: list[str] = list(self.errors)
        planned: dict[str, int] = {}
        for entry in self.albums:
            decision = entry.decision or ("filename_fallback" if not self.config.verify else None)
            if decision in {None, "skip_album"}:
                continue
            album = entry.album
            album_group_key = (
                cli._normalise_artist_key(album.artist),
                (album.album or "").strip().casefold(),
                album.year,
            )
            group_counts = {}
            group_counts[album_group_key] = group_counts.get(album_group_key, 0) + 1
            folder_multidisc = album.disc_number is not None and group_counts.get(album_group_key, 0) > 1
            planned_tracks = cli._music_tracks_from_filenames(
                album.tracks,
                disc_number=album.disc_number,
                multi_disc=folder_multidisc,
            )
            dest_artist = album.artist
            dest_album = album.album
            if decision in {"selected", "order_fallback", "filename_titles_fallback"} and entry.selected_candidate_index is not None:
                candidate = entry.candidates[entry.selected_candidate_index]
                dest_artist = candidate.artist
                dest_album = candidate.title
                mb_tracks = musicbrainz.fetch_release_tracks(candidate.mbid)
                if decision == "selected":
                    mapped, reason = cli._map_musicbrainz_tracks(album.tracks, mb_tracks)
                    if mapped is None:
                        warnings.append(f"{album.source.name}: {reason or 'could not map MusicBrainz tracks'}")
                        continue
                    planned_tracks = mapped
                elif decision == "order_fallback":
                    planned_tracks = cli._map_musicbrainz_by_order(album.tracks, mb_tracks)
                else:
                    planned_tracks = cli._music_tracks_from_filenames(
                        album.tracks,
                        disc_number=album.disc_number,
                        multi_disc=folder_multidisc,
                    )
            elif decision == "filename_fallback":
                dest_artist = album.artist
                dest_album = album.album

            final_artist = "Various Artists" if cli._should_use_various_artists(album, dest_artist) else dest_artist
            album_folder = music_util.album_destination(self.config.library, final_artist, dest_album)
            for track in planned_tracks:
                destination = music_util.track_destination(
                    self.config.library,
                    final_artist,
                    dest_album,
                    track.track_number_text,
                    track.track_title,
                    track.ext,
                )
                destination, _collision = cli._resolve_destination(destination, "rename", planned, None)
                if destination is None:
                    continue
                plans.append(
                    MovePlan(
                        source=track.source,
                        destination=destination,
                        mode=self.config.mode,
                        media_type="music",
                        metadata={"artist": final_artist, "album": dest_album, "track_number": track.track_number},
                    )
                )
            if self.config.keep_art:
                artwork = music_util.select_best_artwork(album.images)
                if artwork is not None:
                    destination = album_folder / "cover.jpg"
                    destination, _collision = cli._resolve_destination(destination, "rename", planned, None)
                    if destination is not None:
                        plans.append(
                            MovePlan(
                                source=artwork,
                                destination=destination,
                                mode=self.config.mode,
                                media_type="music",
                                metadata={"artist": final_artist, "album": dest_album, "type": "artwork"},
                            )
                        )
            if self.config.keep_cue:
                for cue in album.cues:
                    destination = album_folder / cue.name
                    destination, _collision = cli._resolve_destination(destination, "rename", planned, None)
                    if destination is not None:
                        plans.append(
                            MovePlan(
                                source=cue,
                                destination=destination,
                                mode=self.config.mode,
                                media_type="music",
                                metadata={"artist": final_artist, "album": dest_album, "type": "cue"},
                            )
                        )
            if self.config.keep_log:
                for log in album.logs:
                    destination = album_folder / log.name
                    destination, _collision = cli._resolve_destination(destination, "rename", planned, None)
                    if destination is not None:
                        plans.append(
                            MovePlan(
                                source=log,
                                destination=destination,
                                mode=self.config.mode,
                                media_type="music",
                                metadata={"artist": final_artist, "album": dest_album, "type": "log"},
                            )
                        )
        summary_lines = [
            f"Albums: {len(self.albums)}",
            f"Planned files: {len(plans)}",
            f"Warnings: {len(warnings)}",
        ]
        return PreviewState(plans=plans, warnings=warnings, summary_lines=summary_lines)

    def apply_preview(self, preview: PreviewState) -> ApplyResultState:
        report_path = self.config.library / ".plexify" / "reports" / f"{now_timestamp()}.json"
        apply_mode = self.config.mode == "apply"
        result = execute_plans(
            preview.plans,
            apply=apply_mode,
            copy_mode=self.config.copy_mode,
            on_conflict="rename",
        )
        if not apply_mode:
            write_report(report_path, preview.plans, "dry-run", self.config.copy_mode)
        elif not preview.plans:
            write_report(report_path, [], "apply", self.config.copy_mode)
        summary_lines = [
            f"Moved/copied: {len(result.moved)}",
            f"Skipped: {len(result.skipped)}",
            f"Errors: {len(result.errors)}",
            f"Report path: {format_path(report_path)}",
        ]
        return ApplyResultState(
            result=result,
            report_path=report_path,
            apply_report_path=None,
            summary_lines=summary_lines,
        )
