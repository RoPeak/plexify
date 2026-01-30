from plexify.util import normalize_title


def test_normalize_title_basic_cleaning() -> None:
    assert normalize_title("Rocky & Bullwinkle") == "rocky and bullwinkle"


def test_normalize_title_strips_noise_and_punctuation() -> None:
    value = "Birdman.2014.1080p.BluRay.x264"
    assert normalize_title(value) == "birdman"


def test_normalize_title_handles_roman_numeral_suffix() -> None:
    assert normalize_title("Rocky II") == "rocky"
