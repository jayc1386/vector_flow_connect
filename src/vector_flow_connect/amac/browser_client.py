"""Playwright-driven AMAC client for deterministic bulk crawls.

The raw `/api/pof/fund` endpoint is non-deterministic when hit by stateless
httpx (see DISCOVERY.md "AMAC API session-dependence (2026-05-19)"). Calling
the same endpoint via `fetch()` from inside a warm Playwright browser
session inherits the cookies + headers that gate the backend into stable,
0-duplicate pagination — empirically validated against the DataTables UI
flow.

BrowserClient implements the same `.search(page=, size=, sort=, **filters)
-> dict` shape as `AMACClient` (the `_SearchClient` Protocol in `bulk.py`)
so it's a drop-in replacement for bulk/incremental crawls. Targeted
single-fund lookup keeps using `AMACClient` — for one-off queries the
session overhead isn't justified and ~6% miss is irrelevant.
"""

from __future__ import annotations

import contextlib
import time
from typing import Any

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

INDEX_URL = "http://gs.amac.org.cn/amac-infodisc/res/pof/fund/index.html"
API_PATH = "/amac-infodisc/api/pof/fund"

_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_FETCH_JS = """
async ({path, body}) => {
    const r = await fetch(path, {
        method: 'POST',
        headers: {'Content-Type': 'application/json;charset=UTF-8'},
        body: JSON.stringify(body),
    });
    if (!r.ok) {
        const text = await r.text();
        throw new Error(`HTTP ${r.status}: ${text.slice(0, 500)}`);
    }
    return r.json();
}
"""


class BrowserClient:
    """Sync AMAC client backed by a warm Playwright/chromium session.

    The browser is launched once in `__init__` and held open until `close()`.
    Every `search()` call issues the fetch from inside the page context so
    session cookies and the DataTables-induced backend state are applied.
    """

    def __init__(
        self,
        *,
        headless: bool = True,
        sleep_between_requests: float = 2.0,
        page_load_timeout_ms: int = 60_000,
        user_agent: str = _DEFAULT_USER_AGENT,
    ) -> None:
        self._sleep = sleep_between_requests
        self._pw = sync_playwright().start()
        self._browser: Browser = self._pw.chromium.launch(headless=headless)
        self._ctx: BrowserContext = self._browser.new_context(locale="zh-CN", user_agent=user_agent)
        self._page: Page = self._ctx.new_page()
        self._bootstrap(page_load_timeout_ms)

    def _bootstrap(self, page_load_timeout_ms: int) -> None:
        last_exc: BaseException | None = None
        for _attempt in range(3):
            try:
                self._page.goto(
                    INDEX_URL,
                    wait_until="domcontentloaded",
                    timeout=page_load_timeout_ms,
                )
                break
            except Exception as exc:
                last_exc = exc
                time.sleep(5)
        else:
            assert last_exc is not None
            raise last_exc

        with contextlib.suppress(Exception):
            self._page.wait_for_load_state("networkidle", timeout=20_000)
        self._page.wait_for_timeout(2_000)
        with contextlib.suppress(Exception):
            self._page.evaluate(
                "document.querySelectorAll('.layui-layer-shade,.layui-layer')"
                ".forEach(el => el.remove())"
            )

    def search(
        self,
        *,
        keyword: str = "",
        page: int = 0,
        size: int = 20,
        sort: str | None = None,
        **extra_filters: str,
    ) -> dict[str, Any]:
        body: dict[str, str] = {**extra_filters}
        if keyword:
            body["keyword"] = keyword
        qs = f"?page={page}&size={size}"
        if sort:
            qs += f"&sort={sort}"
        path = API_PATH + qs
        result: dict[str, Any] = self._page.evaluate(_FETCH_JS, {"path": path, "body": body})
        if self._sleep:
            time.sleep(self._sleep)
        return result

    def close(self) -> None:
        try:
            self._ctx.close()
        finally:
            try:
                self._browser.close()
            finally:
                self._pw.stop()

    def __enter__(self) -> BrowserClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
