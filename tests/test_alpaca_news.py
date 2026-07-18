"""Tests for AlpacaNewsFetcher against a stubbed alpaca-py client."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from vector_flow_connect.alpaca.news import (
    _SYMBOLS_PER_REQUEST,
    AlpacaNewsFetcher,
    FetchedNewsArticle,
)
from vector_flow_connect.alpaca.settings import AlpacaCredentials


@dataclass
class FakeNews:
    id: int
    headline: str
    created_at: datetime
    updated_at: datetime
    symbols: list[str]
    author: str = "Benzinga Newsdesk"
    source: str = "benzinga"
    url: str | None = "https://example.com/a"
    summary: str = ""
    content: str = ""


@dataclass
class FakeNewsSet:
    data: dict[str, list[FakeNews]] = field(default_factory=dict)


class FakeNewsClient:
    """Captures every request and returns scripted articles per call."""

    def __init__(self, articles_per_call: list[list[FakeNews]]) -> None:
        self._articles_per_call = articles_per_call
        self.requests: list[Any] = []

    def get_news(self, req: Any) -> FakeNewsSet:
        self.requests.append(req)
        articles = self._articles_per_call[len(self.requests) - 1]
        return FakeNewsSet(data={"news": list(articles)})


def _ts(hour: int) -> datetime:
    return datetime(2026, 7, 1, hour, tzinfo=timezone.utc)


def _make_fetcher(
    articles_per_call: list[list[FakeNews]],
) -> tuple[AlpacaNewsFetcher, FakeNewsClient]:
    fetcher = AlpacaNewsFetcher.from_credentials(
        AlpacaCredentials(api_key="test", secret_key="test")
    )
    fake = FakeNewsClient(articles_per_call)
    fetcher._client = fake  # pyright: ignore[reportAttributeAccessIssue]
    return fetcher, fake


class TestAlpacaNewsFetcher:
    def test_returns_normalized_articles_sorted_by_created_at(self):
        fetcher, fake = _make_fetcher(
            [
                [
                    FakeNews(
                        id=2,
                        headline="B Upgrades Foo",
                        created_at=_ts(10),
                        updated_at=_ts(11),
                        symbols=["FOO", "BAR"],
                    ),
                    FakeNews(
                        id=1,
                        headline="A Maintains Bar",
                        created_at=_ts(9),
                        updated_at=_ts(9),
                        symbols=["BAR"],
                    ),
                ]
            ]
        )

        articles = fetcher.get_news(start=_ts(0), end=_ts(23))

        assert [a.id for a in articles] == [1, 2]
        assert articles[1].symbols == ["FOO", "BAR"]
        assert articles[1].updated_at == _ts(11)
        # no symbols filter → single unfiltered request
        assert len(fake.requests) == 1
        assert fake.requests[0].symbols is None

    def test_limit_none_sent_for_full_pagination_drain(self):
        # The v0.10.0 corp-actions lesson: a non-None limit silently
        # truncates the drain. Lock the request shape.
        fetcher, fake = _make_fetcher([[]])
        fetcher.get_news(start=_ts(0), end=_ts(23))
        assert fake.requests[0].limit is None
        assert fake.requests[0].sort == "asc"

    def test_symbol_chunking_dedups_cross_chunk_articles(self):
        symbols = [f"S{i:03d}" for i in range(_SYMBOLS_PER_REQUEST + 1)]
        shared = FakeNews(
            id=7,
            headline="Roundup mentions many",
            created_at=_ts(12),
            updated_at=_ts(12),
            symbols=["S000", f"S{_SYMBOLS_PER_REQUEST:03d}"],
        )
        # Article 7 mentions a symbol in each chunk → returned by both
        # calls; must arrive once.
        fetcher, fake = _make_fetcher([[shared], [shared]])

        articles = fetcher.get_news(start=_ts(0), end=_ts(23), symbols=symbols)

        assert len(fake.requests) == 2
        assert fake.requests[0].symbols == ",".join(symbols[:_SYMBOLS_PER_REQUEST])
        assert fake.requests[1].symbols == symbols[-1]
        assert [a.id for a in articles] == [7]

    def test_empty_symbols_list_returns_empty_without_calling(self):
        fetcher, fake = _make_fetcher([])
        assert fetcher.get_news(start=_ts(0), end=_ts(23), symbols=[]) == []
        assert fake.requests == []

    def test_empty_content_normalizes_to_none(self):
        fetcher, _ = _make_fetcher(
            [
                [
                    FakeNews(
                        id=1,
                        headline="No body",
                        created_at=_ts(9),
                        updated_at=_ts(9),
                        symbols=["FOO"],
                        content="",
                    )
                ]
            ]
        )
        (article,) = fetcher.get_news(start=_ts(0), end=_ts(23))
        assert article.content is None

    def test_pydantic_shape_locked(self):
        # FetchedNewsArticle.model_config has extra="forbid", frozen=True.
        article = FetchedNewsArticle(
            id=1,
            headline="h",
            author="a",
            source="benzinga",
            url=None,
            summary="",
            created_at=_ts(9),
            updated_at=_ts(9),
            symbols=["FOO"],
        )
        assert article.content is None
        try:
            article.headline = "x"  # pyright: ignore[reportAttributeAccessIssue]
            raised = False
        except Exception:
            raised = True
        assert raised
