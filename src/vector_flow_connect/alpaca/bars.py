"""Alpaca stock-bars fetcher.

Wraps alpaca-py's `StockHistoricalDataClient`. Pure read-side; consumers
write canonical state.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from vector_flow_connect.alpaca.settings import AlpacaCredentials


class FetchedBar(BaseModel):
    """One bar as returned by a `BarFetcher`. Vendor-agnostic shape."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


class AlpacaBarFetcher:
    """Concrete `BarFetcher` backed by alpaca-py's `StockHistoricalDataClient`.

    Constructed via `AlpacaBarFetcher.from_credentials()` so creds flow
    through the consumer's settings layer. The `alpaca-py` import
    happens inside the constructor + `get_bars` so unit tests using
    fakes don't pay the import cost or require live env vars.
    """

    def __init__(self, *, api_key: str, api_secret: str, feed: str) -> None:
        from alpaca.data.historical import StockHistoricalDataClient

        self._client = StockHistoricalDataClient(api_key, api_secret)
        self._feed = feed

    @classmethod
    def from_credentials(cls, credentials: AlpacaCredentials) -> AlpacaBarFetcher:
        return cls(
            api_key=credentials.api_key,
            api_secret=credentials.secret_key,
            feed=credentials.feed,
        )

    def get_bars(
        self,
        *,
        symbols: list[str],
        start: date,
        end: date,
    ) -> list[FetchedBar]:
        from alpaca.data.enums import Adjustment, DataFeed
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

        # Alpaca rejects empty symbol lists; short-circuit so callers
        # can pass through without guard logic.
        if not symbols:
            return []
        req = StockBarsRequest(
            symbol_or_symbols=symbols,
            start=datetime(start.year, start.month, start.day, tzinfo=timezone.utc),
            end=datetime(end.year, end.month, end.day, 23, 59, 59, tzinfo=timezone.utc),
            timeframe=TimeFrame(amount=1, unit=TimeFrameUnit.Day),  # pyright: ignore[reportArgumentType]
            adjustment=Adjustment.ALL,
            feed=DataFeed(self._feed),
        )
        barset = self._client.get_stock_bars(req)
        out: list[FetchedBar] = []
        # BarSet.data: dict[str, list[Bar]]
        for symbol, bars in barset.data.items():  # pyright: ignore[reportAttributeAccessIssue]
            for b in bars:
                out.append(
                    FetchedBar(
                        symbol=symbol,
                        timestamp=b.timestamp,
                        open=Decimal(str(b.open)),
                        high=Decimal(str(b.high)),
                        low=Decimal(str(b.low)),
                        close=Decimal(str(b.close)),
                        volume=Decimal(str(b.volume)),
                    )
                )
        return out
