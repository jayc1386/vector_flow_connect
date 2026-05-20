"""httpx-based client for AMAC's public 私募 disclosure API.

Path A (decided 2026-05-19 — see DISCOVERY.md): index/search via JSON API,
detail enrichment via HTML scraping. No browser required.
"""

from __future__ import annotations

import time

import httpx

from vector_flow_connect.amac._selectors import FUND_LIST_ENDPOINT, detail_url

_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class AMACClient:
    """Synchronous AMAC client. Use as a context manager."""

    def __init__(
        self,
        *,
        timeout: float = 30.0,
        user_agent: str = _DEFAULT_USER_AGENT,
        sleep_between_requests: float = 0.25,
    ) -> None:
        self._http = httpx.Client(
            timeout=timeout,
            headers={
                "User-Agent": user_agent,
                "Accept": "application/json, text/html;q=0.9",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
        )
        self._sleep = sleep_between_requests

    def search(
        self,
        *,
        keyword: str = "",
        page: int = 0,
        size: int = 20,
        sort: str | None = None,
        **extra_filters: str,
    ) -> dict:
        """POST /amac-infodisc/api/pof/fund — returns Spring Page envelope.

        `keyword` does substring match on `fundName`. Empty keyword + empty
        filters returns the full table (paginated). Additional filters
        (e.g. `workingState`, `fundType`) are documented in DISCOVERY.md.

        `sort` is the Spring Pageable form `field,direction`, e.g.
        `"putOnRecordDate,desc"`. Used by the v2 incrementer to walk
        most-recent filings first.
        """
        body: dict[str, str] = {**extra_filters}
        if keyword:
            body["keyword"] = keyword
        params: dict[str, str | int] = {"page": page, "size": size}
        if sort:
            params["sort"] = sort
        resp = self._http.post(
            FUND_LIST_ENDPOINT,
            params=params,
            json=body,
        )
        resp.raise_for_status()
        if self._sleep:
            time.sleep(self._sleep)
        return resp.json()

    def fetch_detail_html(self, internal_id: str) -> str:
        """GET /amac-infodisc/res/pof/fund/{id}.html — returns raw HTML."""
        resp = self._http.get(detail_url(internal_id))
        resp.raise_for_status()
        if self._sleep:
            time.sleep(self._sleep)
        return resp.text

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> AMACClient:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()
