from __future__ import annotations

from dataclasses import replace

from ..sources import musicbrainz


def rank_music_candidates(
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

