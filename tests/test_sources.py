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


def test_wikidata_parse_search_results():
    payload = _fixture("tests/fixtures/wikidata_search.json")
    results = wikidata.parse_search_results(payload)
    assert results[0].qid == "Q8337"


def test_wikidata_parse_entity():
    payload = _fixture("tests/fixtures/wikidata_entity.json")
    film = wikidata.parse_entity("Q8337", payload)
    assert film.title == "The Matrix"
    assert film.year == 1999
    assert film.is_film
