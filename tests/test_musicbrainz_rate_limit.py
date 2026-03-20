from __future__ import annotations

import requests

from plexify.sources import musicbrainz


class _FakeResponse:
    status_code = 200

    def json(self):
        return {"releases": []}

    def raise_for_status(self):
        return None


def test_rate_limit_sleeps(monkeypatch) -> None:
    calls: list[float] = []
    times = iter([10.2, 11.0])

    def _fake_monotonic() -> float:
        return next(times)

    def _fake_sleep(duration: float) -> None:
        calls.append(duration)

    musicbrainz._reset_state()
    musicbrainz._state.last_request = 10.0
    monkeypatch.setattr(musicbrainz.time, "monotonic", _fake_monotonic)
    monkeypatch.setattr(musicbrainz.time, "sleep", _fake_sleep)
    musicbrainz._rate_limit(1.0)
    assert calls and calls[0] >= 0.79


def test_search_sets_unavailable_on_error(monkeypatch) -> None:
    class _FakeSession:
        def get(self, *_args, **_kwargs):
            raise requests.RequestException("offline")

    musicbrainz._reset_state()
    monkeypatch.setattr(musicbrainz, "_session", lambda: _FakeSession())
    results = musicbrainz.search_releases("Artist", "Album")
    assert results == []
    assert musicbrainz.is_available() is False
    assert musicbrainz.unavailable_reason() is not None


def test_availability_recovers_after_cooldown(monkeypatch) -> None:
    musicbrainz._reset_state()
    musicbrainz._set_unavailable("offline", cooldown=1.0)
    monkeypatch.setattr(musicbrainz.time, "monotonic", lambda: 999.0)
    musicbrainz._state.recover_at = 10.0

    assert musicbrainz.is_available() is True
    assert musicbrainz.unavailable_reason() is None


def test_unavailable_warning_is_deduplicated(monkeypatch) -> None:
    messages: list[str] = []
    musicbrainz._reset_state()
    monkeypatch.setattr(musicbrainz.logger, "warning", lambda message, *args, **kwargs: messages.append(str(message)))

    musicbrainz._set_unavailable("offline")
    musicbrainz._set_unavailable("offline again")

    assert messages == ["offline"]
