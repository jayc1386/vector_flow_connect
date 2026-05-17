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
from vector_flow_connect.alpaca.settings import (
    AlpacaCredentials,
    AlpacaTradingCredentials,
)


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
class FakeAnnouncement:
    """Mirrors `alpaca.trading.models.CorporateActionAnnouncement` for
    the field surface the sidecar reads."""

    initiating_symbol: str
    ex_date: date
    declaration_date: date | None


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


class FakeTradingClient:
    """Stub TradingClient that returns pre-canned announcements per
    `(since, until)` window. Also records all requests so tests can
    assert chunking behavior.
    """

    def __init__(
        self,
        announcements: list[FakeAnnouncement] | None = None,
    ) -> None:
        self._announcements = announcements or []
        self.requests: list[Any] = []

    def get_corporate_announcements(self, req: Any) -> list[FakeAnnouncement]:
        self.requests.append(req)
        # Return only announcements whose ex_date falls inside the
        # request window — mirrors the live endpoint's behavior so
        # chunked tests behave realistically.
        since: date = req.since
        until: date = req.until
        return [a for a in self._announcements if since <= a.ex_date <= until]


def _make_fetcher(payload: dict[str, list[Any]]) -> AlpacaCorpActionsFetcher:
    fetcher = AlpacaCorpActionsFetcher.from_credentials(
        AlpacaCredentials(api_key="test", secret_key="test", feed="sip")
    )
    fetcher._client = FakeCorpActionsClient(payload)  # pyright: ignore[reportAttributeAccessIssue]
    return fetcher


def _make_fetcher_with_trading(
    payload: dict[str, list[Any]],
    announcements: list[FakeAnnouncement] | None = None,
) -> AlpacaCorpActionsFetcher:
    fetcher = AlpacaCorpActionsFetcher.from_credentials(
        AlpacaCredentials(api_key="test", secret_key="test", feed="sip"),
        trading_credentials=AlpacaTradingCredentials(
            api_key="trading-test", secret_key="trading-test", paper=True
        ),
    )
    fetcher._client = FakeCorpActionsClient(payload)  # pyright: ignore[reportAttributeAccessIssue]
    fetcher._trading_client = FakeTradingClient(announcements)  # pyright: ignore[reportAttributeAccessIssue]
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
        # No trading client → declared_date is None.
        assert e.declared_date is None

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
            declared_date=date(2024, 12, 28),
            cash_amount=Decimal("0.24"),
        )
        assert valid.declared_date == date(2024, 12, 28)
        with pytest.raises(ValidationError):
            FetchedCorpAction.model_validate({**valid.model_dump(), "extra": "x"})


class TestDeclaredDateSidecar:
    """Sidecar lookups via TradingClient.get_corporate_announcements."""

    def test_dividend_carries_declared_date_when_announcements_match(self):
        fetcher = _make_fetcher_with_trading(
            payload={
                "cash_dividends": [
                    FakeDividend(
                        symbol="AAPL",
                        rate=0.24,
                        ex_date=date(2025, 2, 10),
                        process_date=date(2025, 2, 13),
                    )
                ]
            },
            announcements=[
                FakeAnnouncement(
                    initiating_symbol="AAPL",
                    ex_date=date(2025, 2, 10),
                    declaration_date=date(2025, 2, 6),
                )
            ],
        )
        events = fetcher.get_corp_actions(
            symbols=["AAPL"], start=date(2025, 2, 1), end=date(2025, 3, 1)
        )
        assert events[0].declared_date == date(2025, 2, 6)

    def test_dividend_declared_date_none_when_no_match(self):
        # Announcement is for a different symbol → no match.
        fetcher = _make_fetcher_with_trading(
            payload={
                "cash_dividends": [
                    FakeDividend(
                        symbol="AAPL",
                        rate=0.24,
                        ex_date=date(2025, 2, 10),
                        process_date=date(2025, 2, 13),
                    )
                ]
            },
            announcements=[
                FakeAnnouncement(
                    initiating_symbol="MSFT",
                    ex_date=date(2025, 2, 10),
                    declaration_date=date(2025, 2, 6),
                )
            ],
        )
        events = fetcher.get_corp_actions(
            symbols=["AAPL"], start=date(2025, 2, 1), end=date(2025, 3, 1)
        )
        assert events[0].declared_date is None

    def test_announcement_with_blank_initiating_symbol_is_skipped(self):
        # Stock dividends in the announcements endpoint have
        # initiating_symbol="" and target_symbol set; we don't ingest
        # those so they should be dropped from the lookup, not crash
        # the join.
        fetcher = _make_fetcher_with_trading(
            payload={
                "cash_dividends": [
                    FakeDividend(
                        symbol="AAPL",
                        rate=0.24,
                        ex_date=date(2025, 2, 10),
                        process_date=date(2025, 2, 13),
                    )
                ]
            },
            announcements=[
                FakeAnnouncement(
                    initiating_symbol="",  # stock dividend; no initiator
                    ex_date=date(2025, 2, 10),
                    declaration_date=date(2025, 1, 20),
                ),
                FakeAnnouncement(
                    initiating_symbol="AAPL",
                    ex_date=date(2025, 2, 10),
                    declaration_date=date(2025, 2, 6),
                ),
            ],
        )
        events = fetcher.get_corp_actions(
            symbols=["AAPL"], start=date(2025, 2, 1), end=date(2025, 3, 1)
        )
        # The non-blank match wins.
        assert events[0].declared_date == date(2025, 2, 6)

    def test_announcement_with_no_declaration_date_is_skipped(self):
        fetcher = _make_fetcher_with_trading(
            payload={
                "cash_dividends": [
                    FakeDividend(
                        symbol="AAPL",
                        rate=0.24,
                        ex_date=date(2025, 2, 10),
                        process_date=date(2025, 2, 13),
                    )
                ]
            },
            announcements=[
                FakeAnnouncement(
                    initiating_symbol="AAPL",
                    ex_date=date(2025, 2, 10),
                    declaration_date=None,  # rare but possible
                ),
            ],
        )
        events = fetcher.get_corp_actions(
            symbols=["AAPL"], start=date(2025, 2, 1), end=date(2025, 3, 1)
        )
        assert events[0].declared_date is None

    def test_announcements_request_chunks_at_90_days(self):
        # Window: 2025-01-01 → 2025-06-01 (~152 days, needs 2 chunks).
        fetcher = _make_fetcher_with_trading(
            payload={"cash_dividends": []},
            announcements=[],
        )
        fetcher.get_corp_actions(symbols=["AAPL"], start=date(2025, 1, 1), end=date(2025, 6, 1))
        assert fetcher._trading_client is not None  # pyright: ignore[reportAttributeAccessIssue]
        requests = fetcher._trading_client.requests  # pyright: ignore[reportAttributeAccessIssue]
        # Two chunks: [Jan 1, Mar 31] then [Apr 1, Jun 1].
        assert len(requests) == 2
        first, second = requests
        assert first.since == date(2025, 1, 1)
        assert first.until == date(2025, 3, 31)
        assert second.since == date(2025, 4, 1)
        assert second.until == date(2025, 6, 1)
        # Each chunk respects the 90-day cap.
        for r in requests:
            assert (r.until - r.since).days < 90

    def test_announcements_request_single_chunk_when_window_under_90_days(self):
        fetcher = _make_fetcher_with_trading(
            payload={"cash_dividends": []},
            announcements=[],
        )
        fetcher.get_corp_actions(symbols=["AAPL"], start=date(2025, 1, 1), end=date(2025, 2, 28))
        assert fetcher._trading_client is not None  # pyright: ignore[reportAttributeAccessIssue]
        requests = fetcher._trading_client.requests  # pyright: ignore[reportAttributeAccessIssue]
        assert len(requests) == 1

    def test_no_trading_client_means_no_announcements_call(self):
        fetcher = _make_fetcher({"cash_dividends": []})
        # No _trading_client attribute means the sidecar code path
        # is skipped entirely; nothing to assert except no crash.
        fetcher.get_corp_actions(symbols=["AAPL"], start=date(2025, 1, 1), end=date(2025, 12, 31))
        assert fetcher._trading_client is None  # pyright: ignore[reportAttributeAccessIssue]

    def test_split_can_carry_declared_date(self):
        # Splits can also have declaration_date in the announcements
        # endpoint. If the lookup has it, plumb it through.
        fetcher = _make_fetcher_with_trading(
            payload={
                "forward_splits": [
                    FakeForwardSplit(
                        symbol="NVDA",
                        old_rate=1,
                        new_rate=10,
                        ex_date=date(2024, 6, 10),
                        process_date=date(2024, 6, 11),
                    )
                ]
            },
            announcements=[
                FakeAnnouncement(
                    initiating_symbol="NVDA",
                    ex_date=date(2024, 6, 10),
                    declaration_date=date(2024, 5, 22),
                )
            ],
        )
        events = fetcher.get_corp_actions(
            symbols=["NVDA"], start=date(2024, 6, 1), end=date(2024, 6, 30)
        )
        assert events[0].declared_date == date(2024, 5, 22)
