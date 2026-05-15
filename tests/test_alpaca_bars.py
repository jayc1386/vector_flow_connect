"""Tests for AlpacaBarFetcher against a stubbed alpaca-py client."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from vector_flow_connect.alpaca.bars import AlpacaBarFetcher, FetchedBar
from vector_flow_connect.alpaca.settings import AlpacaCredentials


@dataclass
class FakeBar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class FakeBarSet:
    data: dict[str, list[FakeBar]]


class FakeStockClient:
    """Captures the request and returns scripted bars per symbol."""

    def __init__(self, bars_by_symbol: dict[str, list[FakeBar]]) -> None:
        self._bars_by_symbol = bars_by_symbol
        self.last_request: Any = None

    def get_stock_bars(self, req: Any) -> FakeBarSet:
        self.last_request = req
        return FakeBarSet(data=dict(self._bars_by_symbol))


def _make_fetcher(bars_by_symbol: dict[str, list[FakeBar]]) -> AlpacaBarFetcher:
    fetcher = AlpacaBarFetcher.from_credentials(
        AlpacaCredentials(api_key="test", secret_key="test", feed="sip")
    )
    fetcher._client = FakeStockClient(bars_by_symbol)  # pyright: ignore[reportAttributeAccessIssue]
    return fetcher


class TestAlpacaBarFetcher:
    def test_returns_fetched_bars(self):
        fetcher = _make_fetcher(
            {
                "SPY": [
                    FakeBar(
                        timestamp=datetime(2025, 1, 2, tzinfo=UTC),
                        open=590.0,
                        high=595.5,
                        low=589.0,
                        close=594.25,
                        volume=12345678,
                    )
                ]
            }
        )

        bars = fetcher.get_bars(symbols=["SPY"], start=date(2025, 1, 2), end=date(2025, 1, 2))

        assert len(bars) == 1
        assert bars[0].symbol == "SPY"
        assert bars[0].timestamp == datetime(2025, 1, 2, tzinfo=UTC)
        assert bars[0].open == Decimal("590.0")
        assert bars[0].close == Decimal("594.25")
        assert bars[0].volume == Decimal("12345678")

    def test_pydantic_shape_locked(self):
        # FetchedBar.model_config has extra="forbid", frozen=True.
        # Confirms the API contract doesn't silently accept unknown fields.
        bar = FetchedBar(
            symbol="SPY",
            timestamp=datetime(2025, 1, 2, tzinfo=UTC),
            open=Decimal("1"),
            high=Decimal("1"),
            low=Decimal("1"),
            close=Decimal("1"),
            volume=Decimal("1"),
        )
        import pytest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            FetchedBar.model_validate({**bar.model_dump(), "extra_field": "x"})

    def test_multiple_symbols_flattened(self):
        fetcher = _make_fetcher(
            {
                "SPY": [
                    FakeBar(
                        timestamp=datetime(2025, 1, 2, tzinfo=UTC),
                        open=590,
                        high=595,
                        low=589,
                        close=594,
                        volume=1,
                    )
                ],
                "QQQ": [
                    FakeBar(
                        timestamp=datetime(2025, 1, 2, tzinfo=UTC),
                        open=520,
                        high=525,
                        low=519,
                        close=524,
                        volume=2,
                    )
                ],
            }
        )
        bars = fetcher.get_bars(
            symbols=["SPY", "QQQ"], start=date(2025, 1, 2), end=date(2025, 1, 2)
        )
        symbols = {b.symbol for b in bars}
        assert symbols == {"SPY", "QQQ"}

    def test_empty_symbols_short_circuits(self):
        # Alpaca rejects empty symbol lists; the fetcher should never
        # even call the client.
        fetcher = _make_fetcher({"SPY": []})
        bars = fetcher.get_bars(symbols=[], start=date(2025, 1, 2), end=date(2025, 1, 2))
        assert bars == []
        assert fetcher._client.last_request is None  # pyright: ignore[reportAttributeAccessIssue]

    def test_request_uses_split_adjusted_feed(self):
        from alpaca.data.enums import Adjustment, DataFeed

        fetcher = _make_fetcher({"SPY": []})
        fetcher.get_bars(symbols=["SPY"], start=date(2025, 1, 2), end=date(2025, 1, 2))
        req = fetcher._client.last_request  # pyright: ignore[reportAttributeAccessIssue]
        # Critical contract: bars come back split-adjusted per Plan 0017.
        assert req.adjustment == Adjustment.ALL
        assert req.feed == DataFeed.SIP

    def test_from_credentials_uses_provided_feed(self):
        fetcher = AlpacaBarFetcher.from_credentials(
            AlpacaCredentials(api_key="k", secret_key="s", feed="iex")
        )
        assert fetcher._feed == "iex"
