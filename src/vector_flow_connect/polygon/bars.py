"""Polygon (Massive) daily stock-bars fetcher.

Second bars source (after Alpaca), motivated by the SPY 2016-2018
adjusted-close divergence vs CRSP-standard numbers. Wraps Polygon's
aggregates endpoint `/v2/aggs/ticker/{ticker}/range/1/day/{from}/{to}`.

Adjustment basis caveat: Polygon's `adjusted=true` adjusts for **splits
only**, whereas Alpaca's `adjustment='all'` adjusts for splits **and**
dividends. The consumer comparing the two adjusted_close series must
account for this basis difference (it is itself part of what the
SPY-reconciliation surfaces). The fetcher returns Polygon's value
faithfully; interpretation is downstream.

Aggregates are per-ticker (the ticker is in the path), so `get_bars`
loops the symbol list — fine for the SPY-only v1 scope.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

# Vendor-agnostic shared shape (v0 flat layout under alpaca/); reused here.
from vector_flow_connect.alpaca.bars import FetchedBar
from vector_flow_connect.polygon._client import PolygonRestClient
from vector_flow_connect.polygon.settings import PolygonCredentials


class PolygonBarFetcher:
    """Concrete (structural) `BarFetcher` backed by Polygon's aggregates
    endpoint. Construct via `from_credentials()`, or inject a fake
    `client` (any object exposing `paginate(path, params)`) in tests.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        client: Any | None = None,
        adjusted: bool = True,
        rate_limit_sleep_secs: float = 12.0,
    ) -> None:
        if client is None:
            if not api_key:
                raise ValueError("api_key is required when no client is injected")
            client = PolygonRestClient(api_key=api_key, rate_limit_sleep_secs=rate_limit_sleep_secs)
        self._client = client
        self._adjusted = adjusted

    @classmethod
    def from_credentials(cls, credentials: PolygonCredentials) -> PolygonBarFetcher:
        return cls(api_key=credentials.api_key)

    def get_bars(
        self,
        *,
        symbols: list[str],
        start: date,
        end: date,
    ) -> list[FetchedBar]:
        out: list[FetchedBar] = []
        for raw_symbol in symbols:
            symbol = raw_symbol.upper()
            path = f"/v2/aggs/ticker/{symbol}/range/1/day/{start.isoformat()}/{end.isoformat()}"
            for row in self._client.paginate(
                path,
                {"adjusted": "true" if self._adjusted else "false", "sort": "asc", "limit": 50000},
            ):
                ts_ms = row.get("t")
                if ts_ms is None:
                    continue
                out.append(
                    FetchedBar(
                        symbol=symbol,
                        timestamp=datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc),
                        open=Decimal(str(row["o"])),
                        high=Decimal(str(row["h"])),
                        low=Decimal(str(row["l"])),
                        close=Decimal(str(row["c"])),
                        volume=Decimal(str(row.get("v", 0))),
                    )
                )
        return out
