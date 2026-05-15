"""Tests for AlpacaCorpActionsFetcher against a stubbed alpaca-py client."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from vector_flow_connect.alpaca.corp_actions import (
    AlpacaCorpActionsFetcher,
    FetchedCorpAction,
)
from vector_flow_connect.alpaca.settings import AlpacaCredentials


@dataclass
class FakeDividend:
    symbol: str
    rate: float
    ex_date: date
    process_date: date
    record_date: date | None = None
    payable_date: date | None = None
    cusip: str = ""
    id: str = ""
    special: bool = False
    foreign: bool = False


@dataclass
class FakeForwardSplit:
    symbol: str
    old_rate: float
    new_rate: float
    ex_date: date
    process_date: date
    record_date: date | None = None
    payable_date: date | None = None
    cusip: str = ""
    id: str = ""


@dataclass
class FakeReverseSplit:
    symbol: str
    old_rate: float
    new_rate: float
    ex_date: date
    process_date: date
    record_date: date | None = None
    payable_date: date | None = None
    old_cusip: str = ""
    new_cusip: str = ""
    id: str = ""


@dataclass
class FakeCorpActionsResult:
    data: dict[str, list[Any]]


class FakeCorpActionsClient:
    def __init__(self, payload: dict[str, list[Any]]) -> None:
        self._payload = payload
        self.last_request: Any = None

    def get_corporate_actions(self, req: Any) -> FakeCorpActionsResult:
        self.last_request = req
        return FakeCorpActionsResult(data=dict(self._payload))


def _make_fetcher(payload: dict[str, list[Any]]) -> AlpacaCorpActionsFetcher:
    fetcher = AlpacaCorpActionsFetcher.from_credentials(
        AlpacaCredentials(api_key="test", secret_key="test", feed="sip")
    )
    fetcher._client = FakeCorpActionsClient(payload)  # pyright: ignore[reportAttributeAccessIssue]
    return fetcher


class TestAlpacaCorpActionsFetcher:
    def test_normalizes_dividend(self):
        fetcher = _make_fetcher(
            {
                "cash_dividends": [
                    FakeDividend(
                        symbol="AAPL",
                        rate=0.24,
                        ex_date=date(2025, 2, 7),
                        process_date=date(2025, 2, 10),
                        record_date=date(2025, 2, 10),
                        payable_date=date(2025, 2, 13),
                        cusip="037833100",
                        id="div-1",
                    )
                ]
            }
        )
        events = fetcher.get_corp_actions(
            symbols=["AAPL"], start=date(2025, 1, 1), end=date(2025, 3, 1)
        )
        assert len(events) == 1
        e = events[0]
        assert e.event_type == "dividend"
        assert e.cash_amount == Decimal("0.24")
        assert e.cusip == "037833100"
        assert e.external_id == "div-1"
        assert e.is_special is False

    def test_dividend_empty_cusip_becomes_none(self):
        fetcher = _make_fetcher(
            {
                "cash_dividends": [
                    FakeDividend(
                        symbol="AAPL",
                        rate=0.24,
                        ex_date=date(2025, 2, 7),
                        process_date=date(2025, 2, 10),
                        cusip="",
                    )
                ]
            }
        )
        events = fetcher.get_corp_actions(
            symbols=["AAPL"], start=date(2025, 1, 1), end=date(2025, 3, 1)
        )
        assert events[0].cusip is None

    def test_normalizes_forward_split(self):
        fetcher = _make_fetcher(
            {
                "forward_splits": [
                    FakeForwardSplit(
                        symbol="NVDA",
                        old_rate=1,
                        new_rate=10,
                        ex_date=date(2024, 6, 10),
                        process_date=date(2024, 6, 11),
                        id="fs-1",
                    )
                ]
            }
        )
        events = fetcher.get_corp_actions(
            symbols=["NVDA"], start=date(2024, 1, 1), end=date(2024, 12, 31)
        )
        assert len(events) == 1
        e = events[0]
        assert e.event_type == "split"
        assert e.split_ratio_from == Decimal("1")
        assert e.split_ratio_to == Decimal("10")
        # Direction sanity: forward = to > from.
        assert e.split_ratio_to > e.split_ratio_from  # pyright: ignore[reportOperatorIssue]

    def test_reverse_split_preserves_old_and_new_cusip(self):
        fetcher = _make_fetcher(
            {
                "reverse_splits": [
                    FakeReverseSplit(
                        symbol="OLDX",
                        old_rate=10,
                        new_rate=1,
                        ex_date=date(2024, 3, 15),
                        process_date=date(2024, 3, 16),
                        old_cusip="111",
                        new_cusip="222",
                        id="rs-1",
                    )
                ]
            }
        )
        events = fetcher.get_corp_actions(
            symbols=["OLDX"], start=date(2024, 1, 1), end=date(2024, 12, 31)
        )
        assert events[0].cusip == "111"
        assert events[0].new_cusip == "222"
        # Direction sanity: reverse = from > to.
        assert events[0].split_ratio_from > events[0].split_ratio_to  # pyright: ignore[reportOperatorIssue]

    def test_skips_unknown_event_types(self):
        fetcher = _make_fetcher(
            {
                "spin_offs": [object()],  # not a v1 type
                "cash_dividends": [
                    FakeDividend(
                        symbol="AAPL",
                        rate=0.5,
                        ex_date=date(2025, 1, 1),
                        process_date=date(2025, 1, 2),
                    )
                ],
            }
        )
        events = fetcher.get_corp_actions(
            symbols=["AAPL"], start=date(2025, 1, 1), end=date(2025, 12, 31)
        )
        # Only the dividend survives.
        assert len(events) == 1
        assert events[0].event_type == "dividend"

    def test_empty_symbols_short_circuits(self):
        fetcher = _make_fetcher({"cash_dividends": []})
        events = fetcher.get_corp_actions(
            symbols=[], start=date(2025, 1, 1), end=date(2025, 12, 31)
        )
        assert events == []
        assert fetcher._client.last_request is None  # pyright: ignore[reportAttributeAccessIssue]

    def test_pydantic_shape_locked(self):
        import pytest
        from pydantic import ValidationError

        valid = FetchedCorpAction(
            symbol="AAPL",
            event_type="dividend",
            ex_date=date(2025, 1, 1),
            process_date=date(2025, 1, 2),
            cash_amount=Decimal("0.24"),
        )
        with pytest.raises(ValidationError):
            FetchedCorpAction.model_validate({**valid.model_dump(), "extra": "x"})
