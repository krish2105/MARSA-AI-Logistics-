"""Shared HTTP layer for every ingestor.

Three things every scraper in this project needs, implemented once:

1. **Rate limiting** — a token bucket, because both upstreams are public
   services we do not own. CROSS publishes no rate limit, which is a reason to
   be more conservative rather than less.
2. **Retry with backoff and jitter** — transient 5xx and connection resets are
   normal over a multi-thousand-request scrape. `Retry-After` is honoured when
   the server sends it.
3. **An on-disk response cache** — this is what makes a long scrape resumable.
   A run that dies at request 2,400 of 3,000 resumes for free instead of
   re-hammering the source, and re-runs during development cost nothing.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from marsa.logging import get_logger

log = get_logger(__name__)

# Identify the client honestly. A scraper that pretends to be a browser is
# harder for an operator to contact, block, or rate-limit fairly.
USER_AGENT = (
    "MarsaAI-Research/0.2 (adaptive-RAG trade research project; "
    "+https://github.com/krish2105/MARSA-AI-Logistics-)"
)


class RateLimitedError(httpx.HTTPError):
    """429 or 503 — retryable, and the caller should slow down."""


class TransientUpstreamError(httpx.HTTPError):
    """5xx — the server's problem, worth retrying.

    Deliberately distinct from `httpx.HTTPStatusError`: a 4xx means *our*
    request is malformed, and retrying it five times just wastes a public
    service's capacity to arrive at the same answer.
    """


class UpstreamShapeError(RuntimeError):
    """The response parsed, but did not look like what the code expects.

    Raised loudly rather than allowing a silently-empty corpus, which is the
    failure mode that wastes the most time downstream.
    """


@dataclass
class TokenBucket:
    """Classic token bucket. `rate` tokens accrue per second, capped at `burst`."""

    rate: float
    burst: float = 1.0
    _tokens: float = field(default=0.0, init=False)
    _last: float = field(default_factory=time.monotonic, init=False)

    def __post_init__(self) -> None:
        self._tokens = self.burst

    def acquire(self) -> float:
        """Block until a token is available. Returns seconds slept."""
        now = time.monotonic()
        self._tokens = min(self.burst, self._tokens + (now - self._last) * self.rate)
        self._last = now
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return 0.0
        wait = (1.0 - self._tokens) / self.rate
        time.sleep(wait)
        self._last = time.monotonic()
        self._tokens = 0.0
        return wait


class ResponseCache:
    """Content-addressed cache of raw response bodies."""

    def __init__(self, root: Path, *, enabled: bool = True) -> None:
        self.root = root
        self.enabled = enabled
        self.hits = 0
        self.misses = 0

    @staticmethod
    def _key(method: str, url: str, params: dict[str, Any] | None) -> str:
        canonical = json.dumps(
            {"m": method.upper(), "u": url, "p": params or {}},
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(canonical.encode()).hexdigest()

    def _path(self, key: str) -> Path:
        # Two-level fan-out keeps directory sizes sane over thousands of files.
        return self.root / key[:2] / f"{key}.json"

    def get(self, method: str, url: str, params: dict[str, Any] | None) -> str | None:
        if not self.enabled:
            return None
        path = self._path(self._key(method, url, params))
        if path.exists():
            self.hits += 1
            return path.read_text(encoding="utf-8")
        self.misses += 1
        return None

    def put(self, method: str, url: str, params: dict[str, Any] | None, body: str) -> None:
        if not self.enabled:
            return
        path = self._path(self._key(method, url, params))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")


def _log_retry(state: RetryCallState) -> None:
    log.warning(
        "retrying request",
        extra={
            "attempt": state.attempt_number,
            "wait_seconds": round(getattr(state.next_action, "sleep", 0.0), 2),
            "error": str(state.outcome.exception()) if state.outcome else None,
        },
    )


class Fetcher:
    """Polite, cached, retrying HTTP client."""

    def __init__(
        self,
        *,
        requests_per_second: float,
        cache_dir: Path,
        timeout: float = 30.0,
        max_retries: int = 5,
        use_cache: bool = True,
        client: httpx.Client | None = None,
    ) -> None:
        self.bucket = TokenBucket(rate=requests_per_second)
        self.cache = ResponseCache(cache_dir, enabled=use_cache)
        self.max_retries = max_retries
        self.request_count = 0
        self._owns_client = client is None
        self.client = client or httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"},
        )

    # ── context manager ────────────────────────────────────────────────────
    def __enter__(self) -> Fetcher:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    # ── core ───────────────────────────────────────────────────────────────
    def get_text(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> str:
        cached = self.cache.get("GET", url, params)
        if cached is not None:
            return cached

        body = self._request_with_retry(url, params=params, headers=headers)
        self.cache.put("GET", url, params, body)
        return body

    def get_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        raw = self.get_text(url, params=params, headers=headers)
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise UpstreamShapeError(
                f"{url} returned non-JSON (first 200 chars: {raw[:200]!r})"
            ) from exc

    def _request_with_retry(
        self,
        url: str,
        *,
        params: dict[str, Any] | None,
        headers: dict[str, str] | None,
    ) -> str:
        @retry(
            stop=stop_after_attempt(self.max_retries),
            wait=wait_exponential_jitter(initial=1, max=60),
            retry=retry_if_exception_type(
                (RateLimitedError, TransientUpstreamError, httpx.TransportError)
            ),
            before_sleep=_log_retry,
            reraise=True,
        )
        def _do() -> str:
            slept = self.bucket.acquire()
            self.request_count += 1
            response = self.client.get(url, params=params, headers=headers)

            if response.status_code in (429, 503):
                # Honour Retry-After when the server bothers to send it.
                retry_after = response.headers.get("Retry-After")
                if retry_after:
                    # Retry-After may be HTTP-date rather than seconds; if it
                    # will not parse, fall through to the normal backoff.
                    with contextlib.suppress(ValueError):
                        time.sleep(min(float(retry_after), 120))
                raise RateLimitedError(f"rate limited ({response.status_code}) on {url}")

            if response.status_code >= 500:
                raise TransientUpstreamError(f"upstream {response.status_code} on {url}")

            # 4xx other than 429 is a bug in our request, not a blip. This
            # raises HTTPStatusError, which is deliberately absent from the
            # retry predicate above, so it propagates on the first attempt.
            response.raise_for_status()

            log.debug(
                "fetched",
                extra={
                    "url": str(response.url),
                    "status": response.status_code,
                    "bytes": len(response.content),
                    "throttled_seconds": round(slept, 3),
                },
            )
            return response.text

        return _do()

    @property
    def stats(self) -> dict[str, int]:
        return {
            "requests": self.request_count,
            "cache_hits": self.cache.hits,
            "cache_misses": self.cache.misses,
        }
