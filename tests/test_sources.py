import json
from pathlib import Path

from plexify.sources import tvmaze, wikidata


def _fixture(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def test_tvmaze_parse_show_results():
    payload = _fixture("tests/fixtures/tvmaze_search.json")
    shows = tvmaze.parse_show_results(payload)
    assert shows[0].name == "Breaking Bad"
    assert shows[0].id == 169


def test_tvmaze_parse_episode_results():
    payload = _fixture("tests/fixtures/tvmaze_episodes.json")
    eps = tvmaze.parse_episode_results(payload)
    assert eps[0].season == 1
    assert eps[0].number == 2


def test_tvmaze_parse_show_results_ignores_invalid_rows() -> None:
    payload = [
        {"show": {"id": 1, "name": "Valid", "premiered": "2020-01-01"}},
        {"show": {"id": "x", "name": "Bad Id"}},
        {"show": {"id": 3, "name": ""}},
        {"show": {}},
        {},
    ]
    shows = tvmaze.parse_show_results(payload)
    assert len(shows) == 1
    assert shows[0].id == 1


def test_tvmaze_parse_episode_results_ignores_invalid_rows() -> None:
    payload = [
        {"season": 1, "number": 2, "name": "Pilot"},
        {"season": "1", "number": 3, "name": "Bad Season"},
        {"season": 1, "number": None, "name": "Bad Episode"},
        {"season": 1, "number": 4, "name": ""},
        {},
    ]
    episodes = tvmaze.parse_episode_results(payload)
    assert len(episodes) == 1
    assert episodes[0].number == 2


def test_wikidata_parse_search_results():
    payload = _fixture("tests/fixtures/wikidata_search.json")
    results = wikidata.parse_search_results(payload)
    assert results[0].qid == "Q8337"


def test_wikidata_parse_entity():
    payload = _fixture("tests/fixtures/wikidata_entity.json")
    film = wikidata.parse_entity("Q8337", payload)
    assert film.title == "The Matrix"


def test_wikidata_year_prefers_preferred_rank() -> None:
    payload = {
        "entities": {
            "Q1": {
                "labels": {"en": {"value": "Example"}},
                "claims": {
                    "P577": [
                        {"rank": "normal", "mainsnak": {"datavalue": {"value": {"time": "+2014-01-01T00:00:00Z"}}}},
                        {"rank": "preferred", "mainsnak": {"datavalue": {"value": {"time": "+2015-01-01T00:00:00Z"}}}},
                    ]
                },
            }
        }
    }
    film = wikidata.parse_entity("Q1", payload)
    assert film.year == 2015


def test_wikidata_year_uses_earliest_when_no_preferred() -> None:
    payload = {
        "entities": {
            "Q2": {
                "labels": {"en": {"value": "Example"}},
                "claims": {
                    "P577": [
                        {"rank": "normal", "mainsnak": {"datavalue": {"value": {"time": "+2019-01-01T00:00:00Z"}}}},
                        {"rank": "normal", "mainsnak": {"datavalue": {"value": {"time": "+2012-01-01T00:00:00Z"}}}},
                    ]
                },
            }
        }
    }
    film = wikidata.parse_entity("Q2", payload)
    assert film.year == 2012
    assert film.is_film is False
