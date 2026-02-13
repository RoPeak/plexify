from __future__ import annotations

from dataclasses import replace
import re

from rapidfuzz import fuzz

from ..sources import musicbrainz


_SEQUEL_MARKER_RE = re.compile(
    r"\b(?:ii|iii|iv|v|vi|vii|viii|ix|x|part\s*[2-9]|pt\.?\s*[2-9]|vol(?:ume)?\.?\s*[2-9]|[2-9])\b",
    re.IGNORECASE,
)


def _normalise_title(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(value.casefold().split())


def _has_sequel_marker(value: str) -> bool:
    if not value:
        return False
    return _SEQUEL_MARKER_RE.search(value) is not None


def _track_count_fit(requested_count: int, candidate_count: int | None) -> float:
    if candidate_count is None or requested_count <= 0:
        return 0.5
    diff = abs(candidate_count - requested_count)
    if diff == 0:
        return 1.0
    if diff == 1:
        return 0.85
    if diff == 2:
        return 0.7
    if diff <= 4:
        return 0.5
    return 0.2


def _track_count_diff(requested_count: int, candidate_count: int | None) -> int:
    if candidate_count is None:
        return 10_000
    return abs(candidate_count - requested_count)


def _year_fit(requested_year: int | None, candidate_year: int | None) -> float:
    if requested_year is None:
        return 0.5
    if candidate_year is None:
        return 0.3
    diff = abs(candidate_year - requested_year)
    if diff == 0:
        return 1.0
    if diff == 1:
        return 0.85
    if diff <= 3:
        return 0.65
    if diff <= 10:
        return 0.4
    return 0.15


def _year_diff(requested_year: int | None, candidate_year: int | None) -> int:
    if requested_year is None:
        return 0
    if candidate_year is None:
        return 10_000
    return abs(candidate_year - requested_year)


def _candidate_dedupe_key(candidate: musicbrainz.ReleaseCandidate) -> tuple[str, str, int]:
    artist_key = _normalise_title(candidate.artist)
    title_key = _normalise_title(candidate.title)
    track_count_key = candidate.track_count if candidate.track_count is not None else -1
    return artist_key, title_key, track_count_key


def rank_music_candidates(
    candidates: list[musicbrainz.ReleaseCandidate],
    track_count: int,
    requested_title: str | None = None,
    requested_year: int | None = None,
) -> list[musicbrainz.ReleaseCandidate]:
    query_title = _normalise_title(requested_title)
    query_has_sequel = _has_sequel_marker(query_title)
    ranked: list[musicbrainz.ReleaseCandidate] = []
    for cand in candidates:
        raw_score = cand.raw_score if cand.raw_score is not None else cand.score
        raw_score = max(0.0, min(1.0, raw_score))
        track_fit = _track_count_fit(track_count, cand.track_count)
        cand_title = _normalise_title(cand.title)
        title_similarity = 0.0
        if query_title:
            ratio = fuzz.ratio(query_title, cand_title) / 100.0
            token_ratio = fuzz.token_set_ratio(query_title, cand_title) / 100.0
            title_similarity = max(ratio, token_ratio)
        sequel_penalty = 0.0
        if query_title and not query_has_sequel and _has_sequel_marker(cand_title):
            sequel_penalty = -0.18
        year_fit = _year_fit(requested_year, cand.year)
        rank_score = 0.45 * raw_score + 0.30 * title_similarity + 0.17 * track_fit + 0.08 * year_fit + sequel_penalty
        rank_score = min(0.999, max(0.0, rank_score))
        ranked.append(
            replace(
                cand,
                score=rank_score,
                raw_score=raw_score,
                requested_track_count=track_count,
            )
        )
    ranked.sort(
        key=lambda candidate: (
            -candidate.score,
            _track_count_diff(track_count, candidate.track_count),
            _year_diff(requested_year, candidate.year),
            -(candidate.raw_score if candidate.raw_score is not None else candidate.score),
        )
    )
    deduped: list[musicbrainz.ReleaseCandidate] = []
    seen: set[tuple[str, str, int]] = set()
    for candidate in ranked:
        key = _candidate_dedupe_key(candidate)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped
