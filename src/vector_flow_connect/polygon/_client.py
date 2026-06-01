"""Thin httpx wrapper over the Polygon (Massive) REST API.

Handles apiKey auth, `next_url` pagination drain, and a simple
inter-request throttle for the free tier's 5 req/min cap.

The one Polygon gotcha this encapsulates: **`next_url` carries every
query param EXCEPT the apiKey**, so following it naively returns 401.
`paginate` re-attaches the key on every page.

Testable without network: inject any object exposing
`paginate(path, params) -> Iterator[dict]` into a Fetcher, or inject an
`httpx.Client` backed by `httpx.MockTransport` into `PolygonRestClient`.
"""

from __future__ import annotations

import time
from collections.abc import Iterator, Mapping
from typing import Any
from urllib.parse import urlencode

import httpx


class PolygonRestClient:
    """Drains a Polygon list endpoint's `results[]` across all pages.

    `rate_limit_sleep_secs` defaults to 12s (≈5 req/min, the free-tier
    cap) and is applied *between* page requests; paid tiers can pass 0.
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.polygon.io",
        http_client: httpx.Client | None = None,
        rate_limit_sleep_secs: float = 12.0,
        timeout: float = 30.0,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._http = http_client if http_client is not None else httpx.Client(timeout=timeout)
        self._sleep = rate_limit_sleep_secs

    def paginate(self, path: str, params: Mapping[str, Any]) -> Iterator[dict[str, Any]]:
        """Yield every `results[]` row across all pages for `path`.

        First request hits `base_url + path` with `params` + apiKey;
        subsequent requests follow `next_url` (which already encodes the
        cursor + original filters) with only the apiKey re-attached.

        URLs are built fully-formed and passed to `.get()` with no
        `params=` argument — httpx does not reliably merge a `params`
        dict into a URL that already carries a query string (it drops the
        existing query), which would silently lose `next_url`'s cursor.
        """
        first_query = urlencode({**dict(params), "apiKey": self._api_key})
        url: str | None = f"{self._base_url}{path}?{first_query}"
        first = True
        while url:
            if not first and self._sleep:
                time.sleep(self._sleep)
            first = False
            resp = self._http.get(url)
            resp.raise_for_status()
            body = resp.json()
            yield from body.get("results") or []
            next_url = body.get("next_url")
            if not next_url:
                break
            # next_url carries the cursor + filters but not the apiKey.
            sep = "&" if "?" in next_url else "?"
            url = f"{next_url}{sep}apiKey={self._api_key}"
