"""Fetcher behaviour: rate limiting, caching, retry, error classification."""

from __future__ import annotations

import time

import httpx
import pytest
import respx

from marsa.ingestion.fetcher import (
    Fetcher,
    RateLimitedError,
    TokenBucket,
    UpstreamShapeError,
)

URL = "https://example.test/api"


@pytest.fixture
def fetcher(tmp_path):
    with Fetcher(requests_per_second=1000, cache_dir=tmp_path / "cache", max_retries=3) as f:
        yield f


class TestTokenBucket:
    def test_burst_is_immediate(self):
        bucket = TokenBucket(rate=10, burst=1)
        assert bucket.acquire() == 0.0

    def test_throttles_once_burst_is_spent(self):
        bucket = TokenBucket(rate=20, burst=1)
        bucket.acquire()
        started = time.monotonic()
        bucket.acquire()
        # Second token must wait ~1/20s. Allow slack for scheduler noise.
        assert time.monotonic() - started >= 0.03


class TestCache:
    @respx.mock
    def test_second_call_is_served_from_cache(self, fetcher):
        route = respx.get(URL).mock(return_value=httpx.Response(200, json={"ok": 1}))

        first = fetcher.get_json(URL)
        second = fetcher.get_json(URL)

        assert first == second == {"ok": 1}
        assert route.call_count == 1, "cached response should not re-hit the network"
        assert fetcher.cache.hits == 1

    @respx.mock
    def test_differing_params_are_cached_separately(self, fetcher):
        route = respx.get(URL).mock(return_value=httpx.Response(200, json={"ok": 1}))
        fetcher.get_json(URL, params={"page": 1})
        fetcher.get_json(URL, params={"page": 2})
        assert route.call_count == 2

    @respx.mock
    def test_cache_can_be_disabled(self, tmp_path):
        route = respx.get(URL).mock(return_value=httpx.Response(200, json={"ok": 1}))
        with Fetcher(
            requests_per_second=1000, cache_dir=tmp_path / "c", use_cache=False
        ) as f:
            f.get_json(URL)
            f.get_json(URL)
        assert route.call_count == 2


class TestRetry:
    @respx.mock
    def test_recovers_from_transient_500(self, fetcher):
        respx.get(URL).mock(
            side_effect=[
                httpx.Response(500),
                httpx.Response(500),
                httpx.Response(200, json={"ok": True}),
            ]
        )
        assert fetcher.get_json(URL) == {"ok": True}

    @respx.mock
    def test_429_is_retried(self, fetcher):
        respx.get(URL).mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": "0"}),
                httpx.Response(200, json={"ok": True}),
            ]
        )
        assert fetcher.get_json(URL) == {"ok": True}

    @respx.mock
    def test_404_is_not_retried(self, fetcher):
        route = respx.get(URL).mock(return_value=httpx.Response(404))
        with pytest.raises(httpx.HTTPStatusError):
            fetcher.get_json(URL)
        # A 4xx is our bug, not a blip — retrying it just wastes the source's time.
        assert route.call_count == 1

    @respx.mock
    def test_gives_up_after_max_retries(self, fetcher):
        respx.get(URL).mock(return_value=httpx.Response(503))
        with pytest.raises((RateLimitedError, httpx.HTTPStatusError)):
            fetcher.get_json(URL)


class TestErrors:
    @respx.mock
    def test_non_json_raises_shape_error(self, fetcher):
        respx.get(URL).mock(return_value=httpx.Response(200, text="<html>nope</html>"))
        with pytest.raises(UpstreamShapeError, match="non-JSON"):
            fetcher.get_json(URL)
