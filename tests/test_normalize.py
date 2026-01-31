from plexify.util import make_search_query, normalize_title_for_similarity


def test_normalize_title_basic_cleaning() -> None:
    assert normalize_title_for_similarity("Rocky & Bullwinkle") == "rocky and bullwinkle"


def test_normalize_title_strips_noise_and_punctuation() -> None:
    value = "Birdman.2014.1080p.BluRay.x264"
    assert normalize_title_for_similarity(value) == "birdman"


def test_normalize_title_handles_roman_numeral_suffix() -> None:
    assert normalize_title_for_similarity("Rocky II") == "rocky 2"


def test_make_search_query_preserves_roman_numerals() -> None:
    assert make_search_query("Superman II") == "superman ii"
