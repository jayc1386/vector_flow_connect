"""Tests for PolygonBarFetcher against a fake REST client (no network)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from vector_flow_connect.polygon.bars import PolygonBarFetcher


class FakeClient:
    def __init__(self, rows_by_path: dict[str, list[dict[str, Any]]]) -> None:
        self._rows = rows_by_path
        self.calls: list[str] = []

    def paginate(self, path: str, params: dict[str, Any]):
        self.calls.append(path)
        yield from self._rows.get(path, [])


def test_bar_normalization_epoch_ms_to_utc() -> None:
    # 2026-05-01 00:00:00 UTC = 1777593600000 ms
    ts_ms = int(datetime(2026, 5, 1, tzinfo=timezone.utc).timestamp() * 1000)
    rows = {
        "/v2/aggs/ticker/SPY/range/1/day/2026-01-01/2026-12-31": [
            {"t": ts_ms, "o": 500.1, "h": 505.5, "l": 499.0, "c": 503.25, "v": 1234567}
        ]
    }
    fetcher = PolygonBarFetcher(client=FakeClient(rows))
    out = fetcher.get_bars(symbols=["spy"], start=date(2026, 1, 1), end=date(2026, 12, 31))
    assert len(out) == 1
    b = out[0]
    assert b.symbol == "SPY"
    assert b.timestamp == datetime(2026, 5, 1, tzinfo=timezone.utc)
    assert b.open == Decimal("500.1")
    assert b.high == Decimal("505.5")
    assert b.low == Decimal("499.0")
    assert b.close == Decimal("503.25")
    assert b.volume == Decimal("1234567")


def test_bars_loop_per_symbol_and_skip_missing_timestamp() -> None:
    rows = {
        "/v2/aggs/ticker/SPY/range/1/day/2026-01-01/2026-01-31": [
            {"o": 1, "h": 1, "l": 1, "c": 1, "v": 1},  # no "t" → skipped
            {"t": 1777593600000, "o": 2, "h": 2, "l": 2, "c": 2, "v": 2},
        ]
    }
    fetcher = PolygonBarFetcher(client=FakeClient(rows))
    out = fetcher.get_bars(symbols=["SPY"], start=date(2026, 1, 1), end=date(2026, 1, 31))
    assert len(out) == 1
    assert out[0].close == Decimal("2")
