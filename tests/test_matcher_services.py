import re

from plexify.services.movie_matcher import auto_acceptable, confidence_score, search_lost_sequel_marker
from plexify.services.music_matcher import rank_music_candidates
from plexify.services.tv_matcher import normalize_tv_retry_query, tv_confidence_score
from plexify.sources.musicbrainz import ReleaseCandidate


def test_movie_confidence_score_prefers_exact_match_with_year() -> None:
    exact = confidence_score("Superman II", "Superman II", 1980, 1980)
    mismatch = confidence_score("Superman II", "Batman Begins", 1980, 2005)
    assert exact > mismatch


def test_movie_auto_acceptable_blocks_lost_sequel_marker() -> None:
    assert search_lost_sequel_marker("Rocky II", "rocky") is True
    assert (
        auto_acceptable(
            top_confidence=0.99,
            second_confidence=0.8,
            top_year=1979,
            min_confidence=0.9,
            title="Rocky II",
            search_query="rocky",
            target_year=1979,
        )
        is False
    )


def test_tv_confidence_score_rewards_matching_year() -> None:
    with_year = tv_confidence_score("The Office", "The Office", 2005, 2005)
    wrong_year = tv_confidence_score("The Office", "The Office", 2005, 2015)
    assert with_year > wrong_year


def test_tv_retry_query_removes_explicit_season_tokens() -> None:
    season_re = re.compile(r"(?<![A-Za-z0-9])(?:season|series|seaon|seson|seasn)[-_. ]*(\d{1,2})(?![A-Za-z0-9])", re.IGNORECASE)
    assert normalize_tv_retry_query("The Big Bang Theory Seaon 5 cast", season_re) == "the big bang theory cast"


def test_rank_music_candidates_prefers_track_count_match() -> None:
    candidates = [
        ReleaseCandidate("1", "Album", "Artist", 2000, "US", 0.80, 10),
        ReleaseCandidate("2", "Album", "Artist", 2000, "US", 0.75, 12),
    ]
    ranked = rank_music_candidates(candidates, track_count=10)
    assert ranked[0].mbid == "1"
    assert ranked[0].score > ranked[1].score
