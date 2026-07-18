"""Alpaca news fetcher (`/v1beta1/news`, Benzinga-sourced).

Historical news drain over a `[start, end]` window, optionally
filtered to a symbol list. Per-article shape is preserved verbatim —
the full `symbols` list rides along even when the query filters by
symbols, so consumers can compute honest per-article symbol counts.

Pagination (verified against alpaca-py ≥0.31, 2026-07-18):
`NewsClient.get_news` calls `_get_marketdata(page_limit=50,
page_size=50)` — the endpoint's hard 50-items/page cap — and, with
`limit=None` in the request, loops on `next_page_token` until
exhausted. Same silent-truncation trap as corporate actions (v0.10.0):
a non-None `limit` stops the drain at that many items, so this fetcher
always sends `limit=None`.

Symbol filtering: the endpoint takes a comma-separated `symbols`
string. Wide universes are chunked to keep the query string within
URL-length limits; an article mentioning symbols in more than one
chunk arrives once per chunk, so results are de-duplicated on the
vendor article `id`.

Timestamps: `created_at` / `updated_at` are RFC-3339 UTC as returned.
The vendor documents no immutability guarantee for `created_at`;
consumers needing point-in-time semantics should carry `updated_at`
alongside and treat `updated_at > created_at` as a revision marker.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from vector_flow_connect.alpaca.settings import AlpacaCredentials

# Comma-joined symbols ride the query string; 100 tickers ≈ 600 chars,
# comfortably inside common 8k URL limits even with the other params.
_SYMBOLS_PER_REQUEST = 100


class FetchedNewsArticle(BaseModel):
    """One news article as returned by a `NewsFetcher`. Vendor-agnostic
    shape; `symbols` is the article's full mention list regardless of
    any query-side symbol filter."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int  # vendor article id — dedup/cursor key
    headline: str
    author: str
    source: str  # e.g. "benzinga"
    url: str | None = None
    summary: str
    created_at: datetime
    updated_at: datetime
    symbols: list[str]
    content: str | None = None  # HTML body; only when include_content=True


class AlpacaNewsFetcher:
    """Concrete `NewsFetcher` backed by alpaca-py's `NewsClient`.
    Constructed via `AlpacaNewsFetcher.from_credentials()`. The
    `alpaca-py` imports happen inside the constructor so unit tests
    using fakes don't pay the import cost or require live env vars."""

    def __init__(self, *, api_key: str, api_secret: str) -> None:
        from alpaca.data.historical.news import NewsClient

        from vector_flow_connect.alpaca._session import disable_env_proxies

        self._client = disable_env_proxies(NewsClient(api_key, api_secret))

    @classmethod
    def from_credentials(cls, credentials: AlpacaCredentials) -> AlpacaNewsFetcher:
        return cls(api_key=credentials.api_key, api_secret=credentials.secret_key)

    def get_news(
        self,
        *,
        start: datetime,
        end: datetime,
        symbols: list[str] | None = None,
        include_content: bool = False,
    ) -> list[FetchedNewsArticle]:
        """Drain every article in `[start, end]` (inclusive), oldest
        first. `symbols=None` pulls the unfiltered firehose; a symbol
        list is chunked at `_SYMBOLS_PER_REQUEST` per request with
        id-level dedup across chunks."""
        chunks: list[list[str] | None]
        if symbols is None:
            chunks = [None]
        else:
            if not symbols:
                return []
            chunks = [
                list(symbols[i : i + _SYMBOLS_PER_REQUEST])
                for i in range(0, len(symbols), _SYMBOLS_PER_REQUEST)
            ]

        by_id: dict[int, FetchedNewsArticle] = {}
        for chunk in chunks:
            for raw in self._drain_chunk(
                start=start, end=end, symbols=chunk, include_content=include_content
            ):
                article = _normalize_article(raw)
                if article is not None:
                    by_id.setdefault(article.id, article)
        return sorted(by_id.values(), key=lambda a: (a.created_at, a.id))

    def _drain_chunk(
        self,
        *,
        start: datetime,
        end: datetime,
        symbols: list[str] | None,
        include_content: bool,
    ) -> list[Any]:
        from alpaca.data.requests import NewsRequest

        req = NewsRequest(
            start=start,
            end=end,
            symbols=",".join(symbols) if symbols else None,
            sort="asc",
            include_content=include_content,
            # limit=None drains every 50-item page until
            # next_page_token is exhausted — see module docstring.
            limit=None,
        )
        result = self._client.get_news(req)
        return list(result.data.get("news", []))  # pyright: ignore[reportAttributeAccessIssue]


def _normalize_article(article: Any) -> FetchedNewsArticle | None:
    """Convert an alpaca-py `News` object into our vendor-agnostic
    shape. Returns None if the article is malformed in a way we can't
    recover from."""
    try:
        content_raw = getattr(article, "content", None)
        return FetchedNewsArticle(
            id=int(article.id),
            headline=str(article.headline),
            author=str(getattr(article, "author", "") or ""),
            source=str(getattr(article, "source", "") or ""),
            url=getattr(article, "url", None) or None,
            summary=str(getattr(article, "summary", "") or ""),
            created_at=article.created_at,
            updated_at=article.updated_at,
            symbols=[str(s) for s in getattr(article, "symbols", [])],
            content=content_raw if content_raw else None,
        )
    except (AttributeError, ValueError, TypeError):
        return None
