from __future__ import annotations

from dataclasses import dataclass, field
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
from .ui_services import (
    UICandidate,
    apply_tv_folder_season_lock,
    apply_with_streamed_report,
    auto_acceptable,
    build_search_query,
    describe_apply_result,
    describe_cache_context,
    describe_music_verification_warning,
    load_movie_candidates,
    load_tv_candidates,
    map_musicbrainz_by_order,
    map_musicbrainz_tracks,
    music_tracks_from_filenames,
    normalise_music_decision_entry,
    rank_music_candidates,
    resolve_destination,
    resolve_media_type_override,
    should_use_various_artists,
    switch_item_media_type,
    with_title,
)
from .util import ExecutionResult, MovePlan, build_cache_key, iter_video_files, now_timestamp, tv_show_folder_cache_key


def _parse_extensions(extensions: str) -> list[str]:
    return [ext.strip() for ext in extensions.split(",") if ext.strip()]


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
    copy_workers: int = 1
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
    lookup_title: str | None = None
    candidates: list[UICandidate] = field(default_factory=list)
    candidate_states: list[UICandidateState] = field(default_factory=list)
    selected_candidate_index: int | None = None
    manual_candidate: UICandidate | None = None
    skipped: bool = False
    auto_selectable: bool = False
    cache_hit: bool = False
    cache_reusable: bool = False
    cache_context: str = "search result"
    decision_status: str = "pending"
    unresolved_reason: str | None = None
    has_more: bool = False
    next_offset: int = 0
    raw_results: list[Any] | None = None
    warning: str | None = None
    provider: str | None = None
    lookup_status: str = "ok"
    lookup_reason: str | None = None
    attempted_queries: list[str] = field(default_factory=list)
    raw_result_count: int | None = None
    candidate_count: int | None = None
    filtered_count: int | None = None
    search_time: float | None = None
    fetch_time: float | None = None
    total_time: float | None = None

    @property
    def selected_candidate(self) -> UICandidate | None:
        if self.manual_candidate is not None:
            return self.manual_candidate
        if self.selected_candidate_index is None or not self.candidates:
            return None
        if self.selected_candidate_index < 0 or self.selected_candidate_index >= len(self.candidates):
            return None
        return self.candidates[self.selected_candidate_index]

    @property
    def preview_block_reason(self) -> str | None:
        if self.skipped:
            return None
        if self.decision_status == "unresolved":
            return self.unresolved_reason or "No decision selected."
        selected = self.selected_candidate
        if selected is None:
            return self.unresolved_reason or "No selected candidate."
        if self.item.media_type != "tv":
            return None
        season = selected.metadata.get("season") if selected.metadata.get("manual") else self.item.season
        episode = selected.metadata.get("episode") if selected.metadata.get("manual") else self.item.episode
        if season is None or episode is None:
            return "Missing season or episode."
        return None

    @property
    def preview_valid(self) -> bool:
        return self.preview_block_reason is None

    @property
    def resolved(self) -> bool:
        return self.decision_status in {"accepted", "manual", "skipped"}

    @property
    def status_label(self) -> str:
        if self.decision_status in {"accepted", "manual"} and self.preview_block_reason:
            return "blocked"
        return self.decision_status.replace("_", " ")


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
    cached_reason: str | None = None
    cache_context: str = "none"
    decision_status: str = "pending"
    fallback_reason: str | None = None
    unresolved_reason: str | None = None
    warning: str | None = None

    @property
    def resolved(self) -> bool:
        return self.decision_status in {"accepted", "manual", "skipped"}

    @property
    def status_label(self) -> str:
        return self.decision_status.replace("_", " ")


@dataclass(frozen=True)
class PreviewState:
    plans: list[MovePlan]
    warnings: list[str]
    summary_lines: list[str]
    planned_count: int
    skipped_count: int
    unresolved_count: int
    unresolved_items: list[str]

    @property
    def can_apply(self) -> bool:
        return self.unresolved_count == 0


@dataclass(frozen=True)
class BulkApplyResult:
    affected_count: int
    preview_valid_count: int
    blocked_count: int


@dataclass(frozen=True)
class ApplyResultState:
    result: ExecutionResult
    report_path: Path
    apply_report_path: Path | None
    summary_lines: list[str]
    warnings: list[str]


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
        self.items = []
        self.errors = []
        exts = _parse_extensions(self.config.extensions)
        with tvmaze.create_session() as session_tv, wikidata.create_session() as session_wd:
            for path in iter_video_files(self.config.incoming, exts):
                item = infer_item(path)
                item, _override_key = resolve_media_type_override(item, self._cache, self.config.incoming, self._media_type_overrides)
                folder_show_key = tv_show_folder_cache_key(item.path, self.config.incoming) if item.media_type == "tv" else None
                item = apply_tv_folder_season_lock(item, self._cache, folder_show_key)
                if self.config.media_type != "auto" and item.media_type != self.config.media_type:
                    continue
                state = VideoReviewItem(
                    item=item,
                    search_query=build_search_query(item.title, None),
                    cache_key=build_cache_key(item.path, self.config.incoming, item.media_type, item.year),
                )
                self._load_video_candidates(state, session_tv=session_tv, session_wd=session_wd)
                self.items.append(state)

    def _load_video_candidates(
        self,
        state: VideoReviewItem,
        *,
        lookup_item: InferredItem | None = None,
        session_tv: requests.Session | None = None,
        session_wd: requests.Session | None = None,
    ) -> None:
        item = lookup_item or (with_title(state.item, state.lookup_title) if state.lookup_title else state.item)
        state.warning = None
        state.unresolved_reason = None
        try:
            if item.media_type == "tv":
                owns_session = session_tv is None
                session_tv = session_tv or tvmaze.create_session()
                try:
                    page = load_tv_candidates(
                        item=item,
                        session=session_tv,
                        cache=self._cache,
                        incoming_root=self.config.incoming,
                        cache_key=state.cache_key,
                        offset=state.next_offset,
                        raw_results=state.raw_results,
                        search_query=state.search_query,
                        offline=self.config.offline,
                        search_cache=self._tv_search_cache,
                    )
                    if page.candidates:
                        self._maybe_fetch_episode_title(item, page.candidates[0], session_tv)
                finally:
                    if owns_session:
                        session_tv.close()
            else:
                owns_session = session_wd is None
                session_wd = session_wd or wikidata.create_session()
                try:
                    page = load_movie_candidates(
                        item=item,
                        session=session_wd,
                        cache=self._cache,
                        cache_key=state.cache_key,
                        offset=state.next_offset,
                        raw_results=state.raw_results,
                        search_query=state.search_query,
                        offline=self.config.offline,
                        movie_entity_cache=self._movie_entity_cache,
                    )
                finally:
                    if owns_session:
                        session_wd.close()
        except requests.RequestException as exc:
            state.warning = f"{item.path.name}: {exc.__class__.__name__}"
            state.unresolved_reason = "Candidate search failed."
            state.provider = "TVMaze" if item.media_type == "tv" else "Wikidata"
            state.lookup_status = "network_error"
            state.lookup_reason = str(exc) or exc.__class__.__name__
            self.errors.append(state.warning)
            state.candidates = []
            state.candidate_states = []
            state.decision_status = "unresolved"
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
        state.provider = page.provider
        state.lookup_status = page.lookup_status
        state.lookup_reason = page.lookup_reason
        state.attempted_queries = list(page.attempted_queries or [])
        state.raw_result_count = page.raw_result_count
        state.candidate_count = page.candidate_count if page.candidate_count is not None else len(page.candidates)
        state.filtered_count = page.filtered_count
        state.search_time = page.search_time
        state.fetch_time = page.fetch_time
        state.total_time = page.total_time
        state.auto_selectable = bool(
            page.candidates
            and auto_acceptable(
                page.candidates,
                self.config.min_confidence,
                title=state.lookup_title or state.item.title,
                search_query=state.search_query,
                target_year=item.year,
            )
        )
        state.cache_context = describe_cache_context(
            cache_hit=state.cache_hit,
            cache_reusable=state.cache_reusable,
            auto_selectable=state.auto_selectable,
        )
        if not state.candidates:
            state.unresolved_reason = "No candidates available."
            state.decision_status = "unresolved"
        else:
            state.decision_status = "pending"

    def _maybe_fetch_episode_title(self, item: InferredItem, candidate: UICandidate, session: requests.Session) -> None:
        if item.season is None or item.episode is None:
            return
        if candidate.metadata.get("manual"):
            return
        if "episode_title" in candidate.metadata:
            return
        show_id = candidate.metadata.get("id")
        if not show_id:
            return
        episodes = self._episode_cache.get_episodes(int(show_id), session=session)
        for ep in episodes:
            if ep.season == item.season and ep.number == item.episode:
                candidate.metadata["episode_title"] = ep.name
                candidate.confidence = min(1.0, candidate.confidence + 0.1)
                return

    def accept_candidate(self, index: int, candidate_index: int = 0) -> None:
        state = self.items[index]
        if not state.candidates:
            return
        state.selected_candidate_index = max(0, min(candidate_index, len(state.candidates) - 1))
        state.manual_candidate = None
        state.skipped = False
        state.unresolved_reason = None
        state.decision_status = "accepted"

    def skip_item(self, index: int) -> None:
        state = self.items[index]
        state.skipped = True
        state.selected_candidate_index = None
        state.manual_candidate = None
        state.unresolved_reason = None
        state.decision_status = "skipped"

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
        state = self.items[index]
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
        state.manual_candidate = UICandidate(title=title, year=year, source="Manual", confidence=1.0, metadata=metadata)
        state.selected_candidate_index = None
        state.skipped = False
        state.unresolved_reason = None
        state.decision_status = "manual"

    def refine_search(self, index: int, query: str) -> None:
        state = self.items[index]
        if not query.strip():
            return
        lookup_title = query.strip()
        lookup_item = with_title(state.item, lookup_title)
        state.lookup_title = lookup_title
        state.search_query = build_search_query(lookup_title, None)
        state.next_offset = 0
        state.raw_results = None
        state.selected_candidate_index = None
        state.manual_candidate = None
        state.skipped = False
        self._load_video_candidates(state, lookup_item=lookup_item)

    def switch_media_type(self, index: int, media_type: str) -> None:
        state = self.items[index]
        state.item = switch_item_media_type(state.item, media_type)
        state.cache_key = build_cache_key(state.item.path, self.config.incoming, state.item.media_type, state.item.year)
        state.lookup_title = None
        state.search_query = build_search_query(state.item.title, None)
        state.next_offset = 0
        state.raw_results = None
        state.selected_candidate_index = None
        state.manual_candidate = None
        state.skipped = False
        self._load_video_candidates(state)

    def next_page(self, index: int) -> None:
        state = self.items[index]
        if state.has_more:
            self._load_video_candidates(state)

    def apply_choice_to_folder(self, index: int) -> BulkApplyResult:
        state = self.items[index]
        parent = state.item.path.parent
        affected = 0
        for other in self.items:
            if other.item.path.parent == parent and other.item.media_type == state.item.media_type:
                self._copy_video_decision(state, other)
                affected += 1
        return self._bulk_apply_result(affected)

    def apply_choice_to_title_group(self, index: int) -> BulkApplyResult:
        state = self.items[index]
        title_key = (state.item.title.strip().casefold(), state.item.media_type)
        affected = 0
        for other in self.items:
            if (other.item.title.strip().casefold(), other.item.media_type) == title_key:
                self._copy_video_decision(state, other)
                affected += 1
        return self._bulk_apply_result(affected)

    def _copy_video_decision(self, source: VideoReviewItem, target: VideoReviewItem) -> None:
        if source.skipped:
            target.skipped = True
            target.selected_candidate_index = None
            target.manual_candidate = None
            target.unresolved_reason = None
            target.decision_status = "skipped"
            return
        selected = source.selected_candidate
        if selected is None:
            return
        if source.item.media_type == "tv" and target.item.media_type == "tv":
            self._copy_tv_show_decision(source, target, selected)
            return
        if source.manual_candidate is not None:
            target.manual_candidate = UICandidate(
                title=source.manual_candidate.title,
                year=source.manual_candidate.year,
                source=source.manual_candidate.source,
                confidence=source.manual_candidate.confidence,
                metadata=dict(source.manual_candidate.metadata),
            )
            target.selected_candidate_index = None
            target.skipped = False
            target.unresolved_reason = None
            target.decision_status = "manual"
            return
        if target.candidates:
            target.selected_candidate_index = min(source.selected_candidate_index or 0, len(target.candidates) - 1)
            target.manual_candidate = None
            target.skipped = False
            target.unresolved_reason = None
            target.decision_status = "accepted"

    def _copy_tv_show_decision(
        self,
        source: VideoReviewItem,
        target: VideoReviewItem,
        selected: UICandidate,
    ) -> None:
        show_name = selected.metadata.get("name") or selected.title
        show_year = selected.metadata.get("year") or selected.year
        inferred_target = infer_item(target.item.path)
        season = inferred_target.season if inferred_target.season is not None else target.item.season
        if season is None:
            season = source.item.season
        episode = inferred_target.episode if inferred_target.episode is not None else target.item.episode
        episode_end = inferred_target.episode_end if inferred_target.episode_end is not None else target.item.episode_end
        episode_title = inferred_target.episode_title or target.item.episode_title

        target.item = InferredItem(
            path=target.item.path,
            media_type="tv",
            title=show_name,
            year=show_year,
            season=season,
            episode=episode,
            episode_end=episode_end,
            episode_title=episode_title,
        )
        target.search_query = build_search_query(show_name, None)
        propagated = UICandidate(
            title=selected.title,
            year=selected.year,
            source=selected.source,
            confidence=selected.confidence,
            metadata={
                **dict(selected.metadata),
                "name": show_name,
                "year": show_year,
                "season": season,
                "episode": episode,
                "episode_end": episode_end,
                "episode_title": episode_title,
            },
            enrichment=selected.enrichment,
        )
        matched_index = self._matching_candidate_index(target, propagated)
        candidate_state = UICandidateState(
            title=propagated.title,
            year=propagated.year,
            source=propagated.source,
            confidence=propagated.confidence,
            summary=f"{propagated.title} ({propagated.year or 'Unknown'}) [{propagated.confidence:.2f}]",
        )
        if matched_index is None:
            target.candidates.append(propagated)
            target.candidate_states.append(candidate_state)
            target.selected_candidate_index = len(target.candidates) - 1
        else:
            target.candidates[matched_index] = propagated
            target.candidate_states[matched_index] = candidate_state
            target.selected_candidate_index = matched_index
        target.manual_candidate = None
        target.skipped = False
        target.unresolved_reason = None
        target.decision_status = "accepted"

    def _matching_candidate_index(self, target: VideoReviewItem, selected: UICandidate) -> int | None:
        selected_id = selected.metadata.get("id")
        selected_name = str(selected.metadata.get("name") or selected.title).strip().casefold()
        for idx, candidate in enumerate(target.candidates):
            candidate_id = candidate.metadata.get("id")
            if selected_id is not None and candidate_id == selected_id:
                return idx
            candidate_name = str(candidate.metadata.get("name") or candidate.title).strip().casefold()
            if candidate_name == selected_name and candidate.year == selected.year:
                return idx
        return None

    def _bulk_apply_result(self, affected: int) -> BulkApplyResult:
        preview_valid = sum(1 for item in self.items if item.preview_valid)
        blocked = sum(1 for item in self.items if item.preview_block_reason is not None)
        return BulkApplyResult(
            affected_count=affected,
            preview_valid_count=preview_valid,
            blocked_count=blocked,
        )

    def build_preview(self) -> PreviewState:
        plans: list[MovePlan] = []
        warnings: list[str] = []
        unresolved_items: list[str] = []
        planned: dict[str, int] = {}
        skipped = 0
        for state in self.items:
            if state.skipped:
                skipped += 1
                continue
            block_reason = state.preview_block_reason
            if block_reason is not None:
                unresolved_items.append(f"{state.item.path.name}: {block_reason}")
                continue
            selected = state.selected_candidate
            assert selected is not None
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
            destination, _collision = resolve_destination(destination, self.config.on_conflict, planned)
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
        return PreviewState(
            plans=plans,
            warnings=warnings,
            summary_lines=[
                f"Items: {len(self.items)}",
                f"Planned: {len(plans)}",
                f"Skipped: {skipped}",
                f"Unresolved: {len(unresolved_items)}",
                f"Warnings: {len(warnings)}",
                f"Cache hits: {self.stats['cache_hits']}",
            ],
            planned_count=len(plans),
            skipped_count=skipped,
            unresolved_count=len(unresolved_items),
            unresolved_items=unresolved_items,
        )

    def apply_preview(self, preview: PreviewState, *, progress_callback=None, cancel_callback=None) -> ApplyResultState:
        report_path = self.config.library / ".plexify" / "reports" / f"{now_timestamp()}.json"
        apply_report_path: Path | None = None
        if self.config.mode == "apply" and preview.plans:
            apply_report_path = report_path
            result = apply_with_streamed_report(
                preview.plans,
                copy_mode=self.config.copy_mode,
                on_conflict=self.config.on_conflict,
                report_path=report_path,
                progress_callback=progress_callback,
                cancel_callback=cancel_callback,
                copy_workers=self.config.copy_workers,
            )
        else:
            if progress_callback is not None:
                progress_callback(
                    {
                        "phase": "starting",
                        "completed": 0,
                        "total": len(preview.plans),
                        "message": "Applying preview without streamed file operations.",
                    }
                )
            result = execute_plans(
                preview.plans,
                apply=self.config.mode == "apply",
                copy_mode=self.config.copy_mode,
                on_conflict=self.config.on_conflict,
                copy_workers=self.config.copy_workers,
            )
            write_report(report_path, preview.plans if self.config.mode == "dry-run" or not preview.plans else [], self.config.mode, self.config.copy_mode)
            if progress_callback is not None:
                progress_callback(
                    {
                        "phase": "done",
                        "completed": len(preview.plans),
                        "total": len(preview.plans),
                        "message": "Organisation preview finished.",
                    }
                )
        return ApplyResultState(
            result=result,
            report_path=report_path,
            apply_report_path=apply_report_path,
            summary_lines=describe_apply_result(result=result, report_path=report_path, apply_report_path=apply_report_path),
            warnings=list(preview.warnings),
        )


class MusicUIController:
    def __init__(self, config: MusicUIConfig) -> None:
        self.config = config
        self.albums: list[MusicReviewAlbum] = []
        self.errors: list[str] = []
        self._cache = Cache(config.library / ".plexify" / "cache.json")

    def scan(self) -> None:
        self.albums = []
        self.errors = []
        albums, errors = music_util.discover_albums(self.config.source, _parse_extensions(self.config.extensions))
        self.errors.extend(errors)
        available = musicbrainz.is_available()
        verify = self.config.verify and not self.config.offline and available
        session = musicbrainz.create_session() if verify else None
        try:
            for album in albums:
                entry = MusicReviewAlbum(album=album)
                cached = normalise_music_decision_entry(self._cache.get_music(music_util.album_decision_cache_key(album)))
                if cached is not None:
                    entry.cached_decision = str(cached.get("decision") or "")
                    entry.cached_reason = cached.get("reason")
                    entry.cache_context = "cached decision"
                    entry.decision = entry.cached_decision
                entry.warning = describe_music_verification_warning(
                    verify=self.config.verify,
                    offline=self.config.offline,
                    available=available,
                )
                if entry.decision == "skip_album":
                    entry.decision_status = self._music_status(entry)
                    self.albums.append(entry)
                    continue
                if verify and session is not None:
                    candidates = musicbrainz.search_releases(
                        album.artist,
                        album.album,
                        limit=8,
                        session=session,
                        year=album.year,
                    )
                    ranked = rank_music_candidates(candidates, len(album.tracks), album.album, album.year)
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
                    if cached is not None and cached.get("chosen_mbid"):
                        for idx, candidate in enumerate(ranked):
                            if candidate.mbid == cached["chosen_mbid"]:
                                entry.selected_candidate_index = idx
                                break
                        if entry.decision == "selected" and entry.selected_candidate_index is None:
                            entry.unresolved_reason = "Cached release was not found in current candidates."
                elif entry.decision is None and not self.config.verify:
                    entry.decision = "filename_fallback"
                    entry.fallback_reason = "Verification disabled."
                entry.decision_status = self._music_status(entry)
                self.albums.append(entry)
        finally:
            if session is not None:
                session.close()

    def _music_status(self, entry: MusicReviewAlbum) -> str:
        if entry.decision == "skip_album":
            return "skipped"
        if entry.decision in {"filename_fallback", "filename_titles_fallback", "order_fallback"}:
            return "manual"
        if entry.decision == "selected":
            return "accepted"
        if entry.warning or entry.unresolved_reason:
            return "unresolved"
        return "pending"

    def select_candidate(self, index: int, candidate_index: int = 0) -> None:
        album = self.albums[index]
        if not album.candidates:
            return
        album.selected_candidate_index = max(0, min(candidate_index, len(album.candidates) - 1))
        album.decision = "selected"
        album.fallback_reason = None
        album.unresolved_reason = None
        album.decision_status = self._music_status(album)

    def choose_filename_fallback(self, index: int) -> None:
        album = self.albums[index]
        album.decision = "filename_fallback"
        album.fallback_reason = "Use filename titles and original album metadata."
        album.unresolved_reason = None
        album.decision_status = self._music_status(album)

    def choose_filename_titles_fallback(self, index: int) -> None:
        album = self.albums[index]
        album.decision = "filename_titles_fallback"
        album.fallback_reason = "Keep selected release album metadata but use filename-derived track titles."
        album.unresolved_reason = None
        album.decision_status = self._music_status(album)

    def choose_order_fallback(self, index: int) -> None:
        album = self.albums[index]
        album.decision = "order_fallback"
        album.fallback_reason = "Map tracks by order against the selected release."
        album.unresolved_reason = None
        album.decision_status = self._music_status(album)

    def skip_album(self, index: int) -> None:
        album = self.albums[index]
        album.decision = "skip_album"
        album.unresolved_reason = None
        album.decision_status = self._music_status(album)

    def skip_remaining(self, index: int) -> None:
        for album in self.albums[index:]:
            if album.decision is None:
                album.decision = "skip_album"
                album.decision_status = self._music_status(album)

    def build_preview(self) -> PreviewState:
        plans: list[MovePlan] = []
        warnings: list[str] = list(self.errors)
        unresolved_items: list[str] = []
        planned: dict[str, int] = {}
        skipped = 0
        for entry in self.albums:
            decision = entry.decision or ("filename_fallback" if not self.config.verify else None)
            if decision is None:
                unresolved_items.append(f"{entry.album.source.name}: no album decision selected.")
                continue
            if decision == "skip_album":
                skipped += 1
                continue
            album_plans, warning = self._album_plans(entry, decision, planned)
            if album_plans is None:
                unresolved_items.append(f"{entry.album.source.name}: {warning or 'album could not be planned.'}")
                continue
            if warning:
                warnings.append(f"{entry.album.source.name}: {warning}")
            plans.extend(album_plans)
        return PreviewState(
            plans=plans,
            warnings=warnings,
            summary_lines=[
                f"Albums: {len(self.albums)}",
                f"Planned files: {len(plans)}",
                f"Skipped albums: {skipped}",
                f"Unresolved albums: {len(unresolved_items)}",
                f"Warnings: {len(warnings)}",
            ],
            planned_count=len(plans),
            skipped_count=skipped,
            unresolved_count=len(unresolved_items),
            unresolved_items=unresolved_items,
        )

    def _album_plans(
        self,
        entry: MusicReviewAlbum,
        decision: str,
        planned: dict[str, int],
    ) -> tuple[list[MovePlan] | None, str | None]:
        album = entry.album
        planned_tracks = music_tracks_from_filenames(
            album.tracks,
            disc_number=album.disc_number,
            multi_disc=False,
        )
        dest_artist = album.artist
        dest_album = album.album
        warning: str | None = entry.warning
        if decision in {"selected", "order_fallback", "filename_titles_fallback"}:
            if entry.selected_candidate_index is None or not entry.candidates:
                return None, "selected release is missing."
            candidate = entry.candidates[entry.selected_candidate_index]
            dest_artist = candidate.artist
            dest_album = candidate.title
            mb_tracks = musicbrainz.fetch_release_tracks(candidate.mbid)
            if decision == "selected":
                mapped, reason = map_musicbrainz_tracks(album.tracks, mb_tracks)
                if mapped is None:
                    return None, f"track mapping failed: {reason or 'could not map MusicBrainz tracks'}"
                planned_tracks = mapped
            elif decision == "order_fallback":
                planned_tracks = map_musicbrainz_by_order(album.tracks, mb_tracks)
                warning = "Using track order fallback for MusicBrainz mapping."
            else:
                warning = "Using filename-derived track titles with selected release metadata."
        elif decision == "filename_fallback":
            warning = entry.fallback_reason or "Using filename-derived metadata."

        final_artist = "Various Artists" if should_use_various_artists(album, dest_artist) else dest_artist
        album_folder = music_util.album_destination(self.config.library, final_artist, dest_album)
        album_plans: list[MovePlan] = []
        for track in planned_tracks:
            destination = music_util.track_destination(
                self.config.library,
                final_artist,
                dest_album,
                track.track_number_text,
                track.track_title,
                track.ext,
            )
            destination, _collision = resolve_destination(destination, "rename", planned)
            if destination is None:
                continue
            album_plans.append(
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
                destination, _collision = resolve_destination(album_folder / "cover.jpg", "rename", planned)
                if destination is not None:
                    album_plans.append(
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
                destination, _collision = resolve_destination(album_folder / cue.name, "rename", planned)
                if destination is not None:
                    album_plans.append(
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
                destination, _collision = resolve_destination(album_folder / log.name, "rename", planned)
                if destination is not None:
                    album_plans.append(
                        MovePlan(
                            source=log,
                            destination=destination,
                            mode=self.config.mode,
                            media_type="music",
                            metadata={"artist": final_artist, "album": dest_album, "type": "log"},
                        )
                    )
        return album_plans, warning

    def apply_preview(self, preview: PreviewState) -> ApplyResultState:
        report_path = self.config.library / ".plexify" / "reports" / f"{now_timestamp()}.json"
        apply_report_path: Path | None = None
        if self.config.mode == "apply" and preview.plans:
            apply_report_path = report_path
            result = apply_with_streamed_report(preview.plans, copy_mode=self.config.copy_mode, on_conflict="rename", report_path=report_path)
        else:
            result = execute_plans(preview.plans, apply=self.config.mode == "apply", copy_mode=self.config.copy_mode, on_conflict="rename")
            write_report(report_path, preview.plans if self.config.mode == "dry-run" or not preview.plans else [], self.config.mode, self.config.copy_mode)
        return ApplyResultState(
            result=result,
            report_path=report_path,
            apply_report_path=apply_report_path,
            summary_lines=describe_apply_result(result=result, report_path=report_path, apply_report_path=apply_report_path),
            warnings=list(preview.warnings),
        )
