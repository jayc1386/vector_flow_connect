"""Thin httpx wrapper over the Polygon (Massive) REST API.

Handles apiKey auth, `next_url` pagination drain, a **stateful
min-interval throttle** for the free tier's 5 req/min cap, and 429
retry-with-backoff.

The one Polygon gotcha this encapsulates: **`next_url` carries every
query param EXCEPT the apiKey**, so following it naively returns 401.
`paginate` re-attaches the key on every page.

**Throttle is a lever.** `min_request_interval_secs` is the minimum gap
maintained between *any two requests* — across pages, across separate
`paginate()` calls, across symbols (it's stateful on the client, keyed
on the last request time, not per-paginate-call). Default 12s ≈ 5
req/min (free tier). **Set it to `0` to disable throttling entirely**
(paid tier = unlimited). `max_retries` adds a reactive 429 safety net
(honors `Retry-After` when present) so a borderline window still
recovers rather than erroring.

Testable without network: inject any object exposing
`paginate(path, params) -> Iterator[dict]` into a Fetcher; inject an
`httpx.Client` backed by `httpx.MockTransport` into `PolygonRestClient`;
inject `clock` / `sleep` to make the throttle deterministic in tests.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Mapping
from typing import Any
from urllib.parse import urlencode

import httpx


class PolygonRestClient:
    """Drains a Polygon list endpoint's `results[]` across all pages,
    pacing every request to stay under the tier's rate limit.

    `min_request_interval_secs` defaults to 12s (≈5 req/min, the
    free-tier cap) and is the **bypass lever**: pass `0` on a paid tier
    to disable throttling. `max_retries` retries a 429 with backoff.
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.polygon.io",
        http_client: httpx.Client | None = None,
        min_request_interval_secs: float = 12.0,
        max_retries: int = 3,
        timeout: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        # trust_env=False: vendor traffic must not ride operator proxy
        # env vars leaking into tmux-launched workers (qh relay 0067).
        self._http = (
            http_client
            if http_client is not None
            else httpx.Client(timeout=timeout, trust_env=False)
        )
        self._min_interval = min_request_interval_secs
        self._max_retries = max_retries
        self._clock = clock
        self._sleep = sleep
        self._last_request_at: float | None = None

    def _throttle(self) -> None:
        """Maintain >= `min_request_interval_secs` between successive
        requests across ALL paginate() calls + symbols. No-op when the
        interval is <= 0 (paid-tier bypass)."""
        if self._min_interval <= 0:
            return
        if self._last_request_at is not None:
            wait = self._min_interval - (self._clock() - self._last_request_at)
            if wait > 0:
                self._sleep(wait)
        self._last_request_at = self._clock()

    def _get(self, url: str) -> httpx.Response:
        """Throttled GET with 429 retry-with-backoff (honors
        `Retry-After`). Raises on non-2xx (incl. a 429 that outlives
        `max_retries`)."""
        attempt = 0
        while True:
            self._throttle()
            resp = self._http.get(url)
            if resp.status_code != 429 or attempt >= self._max_retries:
                resp.raise_for_status()  # 429 outliving retries surfaces here
                return resp
            retry_after = resp.headers.get("Retry-After", "")
            backoff = (
                float(retry_after)
                if retry_after.replace(".", "", 1).isdigit()
                else max(self._min_interval, 1.0) * (attempt + 1)
            )
            self._sleep(backoff)
            attempt += 1

    def paginate(self, path: str, params: Mapping[str, Any]) -> Iterator[dict[str, Any]]:
        """Yield every `results[]` row across all pages for `path`.

        First request hits `base_url + path` with `params` + apiKey;
        subsequent requests follow `next_url` (which already encodes the
        cursor + original filters) with only the apiKey re-attached.

        URLs are built fully-formed and passed to `.get()` with no
        `params=` argument — httpx does not reliably merge a `params`
        dict into a URL that already carries a query string (it drops the
        existing query), which would silently lose `next_url`'s cursor.
        Every request is paced by `_get` → `_throttle`.
        """
        first_query = urlencode({**dict(params), "apiKey": self._api_key})
        url: str | None = f"{self._base_url}{path}?{first_query}"
        while url:
            body = self._get(url).json()
            yield from body.get("results") or []
            next_url = body.get("next_url")
            if not next_url:
                break
            # next_url carries the cursor + filters but not the apiKey.
            sep = "&" if "?" in next_url else "?"
            url = f"{next_url}{sep}apiKey={self._api_key}"
