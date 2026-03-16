import re

from plexify.services import selection_policy
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


def test_selection_policy_uses_offline_skip_reason() -> None:
    assert selection_policy.no_match_skip_reason(offline=True) == selection_policy.OFFLINE_NO_CACHE_REASON
    assert selection_policy.no_match_skip_reason(offline=False) == selection_policy.NO_CANDIDATES_REASON


def test_selection_policy_trusts_confirmed_and_auto_folder_cache_entries() -> None:
    assert selection_policy.folder_show_cache_entry_is_trusted({"confirmed_by_user": True}) is True
    assert selection_policy.folder_show_cache_entry_is_trusted({"selection_mode": "auto", "manual": False}) is True
    assert selection_policy.folder_show_cache_entry_is_trusted({"selection_mode": "manual", "manual": False}) is False


def test_rank_music_candidates_prefers_track_count_match() -> None:
    candidates = [
        ReleaseCandidate("1", "Album", "Artist", 2000, "US", 0.80, 10),
        ReleaseCandidate("2", "Album", "Artist", 2000, "US", 0.75, 12),
    ]
    ranked = rank_music_candidates(candidates, track_count=10)
    assert ranked[0].mbid == "1"
    assert ranked[0].score > ranked[1].score


def test_rank_music_candidates_penalises_unwanted_sequel_titles() -> None:
    candidates = [
        ReleaseCandidate("1", "Curtain Call", "Eminem", 2005, "US", 0.80, 17),
        ReleaseCandidate("2", "Curtain Call 2", "Eminem", 2022, "US", 0.80, 17),
    ]
    ranked = rank_music_candidates(candidates, track_count=17, requested_title="Curtain Call")
    assert ranked[0].mbid == "1"
    assert ranked[0].score > ranked[1].score


def test_rank_music_candidates_avoids_uniform_one_scores() -> None:
    candidates = [
        ReleaseCandidate("1", "Tapestry", "Carole King", 1971, "US", 0.99, 12, raw_score=0.99),
        ReleaseCandidate("2", "Tapestry", "Carole King", 1999, "US", 0.98, 14, raw_score=0.98),
        ReleaseCandidate("3", "Tapestry 2", "Carole King", 2002, "US", 0.97, 12, raw_score=0.97),
    ]
    ranked = rank_music_candidates(candidates, track_count=12, requested_title="Tapestry")
    assert ranked[0].score < 1.0
    assert len({round(candidate.score, 3) for candidate in ranked}) > 1


def test_rank_music_candidates_prefers_year_closer_match_when_requested() -> None:
    candidates = [
        ReleaseCandidate("1", "Rockferry", "Duffy", 2008, "GB", 0.95, 10, raw_score=0.95),
        ReleaseCandidate("2", "Rockferry", "Duffy", 2006, "GB", 0.95, 10, raw_score=0.95),
    ]
    ranked = rank_music_candidates(candidates, track_count=10, requested_title="Rockferry", requested_year=2008)
    assert ranked[0].mbid == "1"


def test_rank_music_candidates_dedupes_identical_title_and_track_count() -> None:
    candidates = [
        ReleaseCandidate("1", "III", "Take That", 2014, "GB", 0.95, 12, raw_score=0.95),
        ReleaseCandidate("2", "III", "Take That", 2014, "DZ", 0.92, 12, raw_score=0.92),
        ReleaseCandidate("3", "III", "Take That", 2014, "GB", 0.90, 15, raw_score=0.90),
    ]
    ranked = rank_music_candidates(candidates, track_count=12, requested_title="III", requested_year=2014)
    assert [candidate.mbid for candidate in ranked] == ["1", "3"]
