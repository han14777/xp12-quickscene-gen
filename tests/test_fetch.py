"""Overpass mirror rotation and retries (network replaced by fakes)."""

import pytest

from quickscene import fetch


class FakeResponse:
    def __init__(self, status: int, data: dict | None = None):
        self.status_code = status
        self._data = data or {}

    def json(self):
        return self._data


@pytest.fixture
def no_sleep(monkeypatch):
    waits = []
    monkeypatch.setattr(fetch.time, "sleep", waits.append)
    return waits


def fake_post(monkeypatch, replies: dict):
    """replies: url -> list of responses returned in order."""
    calls = []

    def post(url, data, headers, timeout):
        calls.append(url)
        return replies[url].pop(0)
    monkeypatch.setattr(fetch.requests, "post", post)
    return calls


def test_busy_server_falls_through_to_mirror_without_waiting(monkeypatch, no_sleep):
    calls = fake_post(monkeypatch, {"https://a/api": [FakeResponse(502)],
                                    "https://b/api": [FakeResponse(200, {"elements": [1]})]})
    data = fetch.post_overpass(["https://a/api", "https://b/api"], "q", 10, 3, 30)
    assert data == {"elements": [1]}
    assert calls == ["https://a/api", "https://b/api"]
    assert no_sleep == []


def test_waits_only_after_a_full_round_and_grows(monkeypatch, no_sleep):
    calls = fake_post(monkeypatch, {"https://a/api": [FakeResponse(504)] * 3,
                                    "https://b/api": [FakeResponse(502)] * 3})
    with pytest.raises(RuntimeError, match="every server"):
        fetch.post_overpass(["https://a/api", "https://b/api"], "q", 10, 3, 30)
    assert len(calls) == 6
    assert no_sleep == [30, 60]


def test_partial_result_is_retried_not_returned(monkeypatch, no_sleep):
    partial = FakeResponse(200, {"elements": [], "remark": "runtime error: Query timed out in \"query\""})
    fake_post(monkeypatch, {"https://a/api": [partial, FakeResponse(200, {"elements": [2]})]})
    assert fetch.post_overpass("https://a/api", "q", 10, 3, 30) == {"elements": [2]}
    assert no_sleep == [30]


def test_single_url_string_still_works(monkeypatch, no_sleep):
    fake_post(monkeypatch, {"https://a/api": [FakeResponse(200, {"ok": 1})]})
    assert fetch.post_overpass("https://a/api", "q", 10, 1, 30) == {"ok": 1}
    with pytest.raises(RuntimeError, match="empty"):
        fetch.overpass_urls([])
