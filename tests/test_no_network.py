import pytest
import requests


def test_network_calls_are_blocked() -> None:
    with pytest.raises(AssertionError, match="Network calls are forbidden in tests"):
        requests.get("https://example.com", timeout=(1, 1))
