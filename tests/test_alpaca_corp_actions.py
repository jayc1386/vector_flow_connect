"""Tests for AlpacaCorpActionsFetcher against a stubbed alpaca-py client."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
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
    the field surface the fetcher reads (sidecar + v0.8.0 dual-pass).

    `ca_type` / `ca_sub_type` are strings here; the production code
    reads them via `getattr(...).value` and falls back to `str(...)`,
    so plain strings work as fakes. Default values cover the
    pre-0.8.0 sidecar-only test path which only needs
    `(initiating_symbol, ex_date, declaration_date)`."""

    initiating_symbol: str
    ex_date: date
    declaration_date: date | None
    ca_type: str = "dividend"
    ca_sub_type: str = "cash"
    cash: float = 0.0
    old_rate: float = 0.0
    new_rate: float = 0.0
    record_date: date | None = None
    payable_date: date | None = None
    id: str = ""
    initiating_original_cusip: str = ""


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
    `(since, until)` window. Filters by `req.date_type`:

    - EX_DATE (default for v0.7.x callers): filter on ex_date
    - DECLARATION_DATE (v0.8.0 new pass): filter on declaration_date
    """

    def __init__(
        self,
        announcements: list[FakeAnnouncement] | None = None,
    ) -> None:
        self._announcements = announcements or []
        self.requests: list[Any] = []

    def get_corporate_announcements(self, req: Any) -> list[FakeAnnouncement]:
        self.requests.append(req)
        since: date = req.since
        until: date = req.until
        date_type_val = getattr(req.date_type, "value", str(req.date_type)).lower()
        out: list[FakeAnnouncement] = []
        for a in self._announcements:
            if date_type_val == "declaration_date":
                if a.declaration_date is None:
                    continue
                key = a.declaration_date
            else:
                key = a.ex_date
            if since <= key <= until:
                out.append(a)
        return out


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
        # Asserts on the EX_DATE-filtered sidecar requests only; the
        # v0.8.0 DECLARATION_DATE pass also fires (covered by its own tests).
        fetcher = _make_fetcher_with_trading(
            payload={"cash_dividends": []},
            announcements=[],
        )
        fetcher.get_corp_actions(symbols=["AAPL"], start=date(2025, 1, 1), end=date(2025, 6, 1))
        assert fetcher._trading_client is not None  # pyright: ignore[reportAttributeAccessIssue]
        all_requests = fetcher._trading_client.requests  # pyright: ignore[reportAttributeAccessIssue]
        ex_requests = [r for r in all_requests if r.date_type.value == "ex_date"]
        # Two chunks: [Jan 1, Mar 31] then [Apr 1, Jun 1].
        assert len(ex_requests) == 2
        first, second = ex_requests
        assert first.since == date(2025, 1, 1)
        assert first.until == date(2025, 3, 31)
        assert second.since == date(2025, 4, 1)
        assert second.until == date(2025, 6, 1)
        # Each chunk respects the 90-day cap.
        for r in ex_requests:
            assert (r.until - r.since).days < 90

    def test_announcements_request_single_chunk_when_window_under_90_days(self):
        fetcher = _make_fetcher_with_trading(
            payload={"cash_dividends": []},
            announcements=[],
        )
        fetcher.get_corp_actions(symbols=["AAPL"], start=date(2025, 1, 1), end=date(2025, 2, 28))
        assert fetcher._trading_client is not None  # pyright: ignore[reportAttributeAccessIssue]
        all_requests = fetcher._trading_client.requests  # pyright: ignore[reportAttributeAccessIssue]
        ex_requests = [r for r in all_requests if r.date_type.value == "ex_date"]
        assert len(ex_requests) == 1

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
                    ca_type="split",
                    ca_sub_type="stock_split",
                    old_rate=1,
                    new_rate=10,
                )
            ],
        )
        events = fetcher.get_corp_actions(
            symbols=["NVDA"], start=date(2024, 6, 1), end=date(2024, 6, 30)
        )
        assert events[0].declared_date == date(2024, 5, 22)


class TestDualPassV080:
    """v0.8.0 — DECLARATION_DATE pass merged with the existing EX_DATE
    sidecar + market-data flow."""

    def test_future_ex_event_lands_via_declaration_pass(self):
        """NVDA-shaped case: dividend declared on 5/20 with future
        ex_date 6/04. The market-data endpoint (ex_date-filtered) won't
        return it until 6/04, but the DECLARATION_DATE pass picks it up
        via the announcements endpoint."""
        fetcher = _make_fetcher_with_trading(
            payload={"cash_dividends": []},  # market-data sees nothing
            announcements=[
                FakeAnnouncement(
                    initiating_symbol="NVDA",
                    ex_date=date(2026, 6, 4),  # future
                    declaration_date=date(2026, 5, 20),
                    ca_type="dividend",
                    ca_sub_type="cash",
                    cash=0.25,
                ),
            ],
        )
        events = fetcher.get_corp_actions(
            symbols=["NVDA"], start=date(2026, 5, 1), end=date(2026, 5, 31)
        )
        assert len(events) == 1
        e = events[0]
        assert e.symbol == "NVDA"
        assert e.event_type == "dividend"
        assert e.ex_date == date(2026, 6, 4)
        assert e.declared_date == date(2026, 5, 20)
        assert e.cash_amount == Decimal("0.25")

    def test_declaration_pass_fills_null_declared_date(self):
        """If the EX_DATE sidecar missed a row (e.g. cap-truncated)
        but the DECLARATION_DATE pass catches it, the merge fills in
        the declared_date on the existing market-data row."""
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
            announcements=[],
        )
        # Manually inject an announcement seen ONLY by the
        # DECLARATION_DATE pass (declaration_date in lookback window;
        # ex_date NOT in EX_DATE pass window).
        # Trick: set declaration_date in DECLARATION_DATE lookback but
        # ex_date the same as the market-data event so the merge fills.
        # Simpler: just use the same ex_date — sidecar (EX_DATE filter)
        # also returns it; both passes find it; merge no-ops.
        # For a true "sidecar missed" test we'd need the sidecar to
        # filter it out — use a declaration_date outside [start, end]
        # but inside [end-60d, end].
        fetcher._trading_client = FakeTradingClient(  # pyright: ignore[reportAttributeAccessIssue]
            [
                FakeAnnouncement(
                    initiating_symbol="AAPL",
                    ex_date=date(2025, 2, 10),
                    declaration_date=date(2025, 1, 20),  # before start=2025-02-01
                    ca_type="dividend",
                    ca_sub_type="cash",
                    cash=0.24,
                ),
            ]
        )
        events = fetcher.get_corp_actions(
            symbols=["AAPL"], start=date(2025, 2, 1), end=date(2025, 3, 1)
        )
        assert len(events) == 1
        # EX_DATE sidecar window [2/1, 3/1] doesn't include 1/20 declaration → None.
        # DECLARATION_DATE pass window [3/1 - 60d = 2024-12-30, 3/1] DOES include 1/20.
        # Merge fills in 1/20 from announcement pass.
        assert events[0].declared_date == date(2025, 1, 20)

    def test_declaration_pass_skips_other_symbols(self):
        """An announcement for a symbol not in the request's `symbols`
        list must not introduce a phantom event."""
        fetcher = _make_fetcher_with_trading(
            payload={"cash_dividends": []},
            announcements=[
                FakeAnnouncement(
                    initiating_symbol="MSFT",  # not in symbols
                    ex_date=date(2026, 6, 4),
                    declaration_date=date(2026, 5, 20),
                    ca_type="dividend",
                    ca_sub_type="cash",
                    cash=0.5,
                ),
            ],
        )
        events = fetcher.get_corp_actions(
            symbols=["NVDA"], start=date(2026, 5, 1), end=date(2026, 5, 31)
        )
        assert events == []

    def test_declaration_pass_window_is_60_days_back_from_end(self):
        """An announcement declared more than 60 days before `end` is
        outside the DECLARATION_DATE pass window and not picked up."""
        fetcher = _make_fetcher_with_trading(
            payload={"cash_dividends": []},
            announcements=[
                FakeAnnouncement(
                    initiating_symbol="NVDA",
                    ex_date=date(2026, 6, 4),
                    declaration_date=date(2026, 3, 1),  # >60d before end=2026-05-31
                    ca_type="dividend",
                    ca_sub_type="cash",
                    cash=0.25,
                ),
            ],
        )
        events = fetcher.get_corp_actions(
            symbols=["NVDA"], start=date(2026, 5, 1), end=date(2026, 5, 31)
        )
        # Declaration 3/1 is before [5/31 - 60d = 4/1, 5/31] lookback.
        assert events == []

    def test_dual_pass_fires_declaration_date_request(self):
        """Verifies the v0.8.0 DECLARATION_DATE request is actually
        sent to the trading client (orthogonal to the EX_DATE sidecar)."""
        fetcher = _make_fetcher_with_trading(
            payload={"cash_dividends": []},
            announcements=[],
        )
        fetcher.get_corp_actions(symbols=["AAPL"], start=date(2025, 1, 1), end=date(2025, 2, 28))
        assert fetcher._trading_client is not None  # pyright: ignore[reportAttributeAccessIssue]
        all_requests = fetcher._trading_client.requests  # pyright: ignore[reportAttributeAccessIssue]
        decl_requests = [r for r in all_requests if r.date_type.value == "declaration_date"]
        assert len(decl_requests) >= 1
        # The DECLARATION_DATE pass window is [end - 60d, end].
        decl_req = decl_requests[0]
        assert decl_req.since == date(2025, 2, 28) - timedelta(days=60)
        assert decl_req.until == date(2025, 2, 28)

    def test_no_trading_client_skips_declaration_pass(self):
        """When trading credentials are absent the DECLARATION_DATE
        pass is skipped along with the EX_DATE sidecar."""
        fetcher = _make_fetcher(
            payload={
                "cash_dividends": [
                    FakeDividend(
                        symbol="AAPL",
                        rate=0.24,
                        ex_date=date(2025, 2, 10),
                        process_date=date(2025, 2, 13),
                    )
                ]
            }
        )
        events = fetcher.get_corp_actions(
            symbols=["AAPL"], start=date(2025, 2, 1), end=date(2025, 3, 1)
        )
        assert len(events) == 1
        assert events[0].declared_date is None
        assert fetcher._trading_client is None  # pyright: ignore[reportAttributeAccessIssue]


class TestRestatementFilterV090:
    """v0.9.0 — Alpaca's deprecated announcements endpoint encodes some
    retroactive restatement records with `declaration_date =
    (when-the-restatement-was-issued)` rather than the original
    board-vote date, producing temporally-incoherent rows where
    `declared_date > ex_date`. Both the EX_DATE sidecar and the v0.8.0
    DECLARATION_DATE pass reject those at the boundary."""

    def test_ex_date_sidecar_drops_declared_after_ex(self):
        """A market-data event for AAPL ex=2020-03-30 plus a sidecar
        announcement with declaration_date=2021-04-20 (restatement
        record). Sidecar should drop the restatement; the market-data
        event keeps declared_date=None."""
        fetcher = _make_fetcher_with_trading(
            payload={
                "cash_dividends": [
                    FakeDividend(
                        symbol="AAPL",
                        rate=0.20,
                        ex_date=date(2020, 3, 30),
                        process_date=date(2020, 3, 31),
                    )
                ]
            },
            announcements=[
                FakeAnnouncement(
                    initiating_symbol="AAPL",
                    ex_date=date(2020, 3, 30),
                    declaration_date=date(2021, 4, 20),  # restatement record
                ),
            ],
        )
        events = fetcher.get_corp_actions(
            symbols=["AAPL"], start=date(2020, 3, 1), end=date(2020, 5, 1)
        )
        assert len(events) == 1
        assert events[0].declared_date is None  # restatement was dropped

    def test_declaration_pass_drops_declared_after_ex(self):
        """The v0.8.0 DECLARATION_DATE pass also filters restatement
        records — they'd otherwise land as first-class FetchedCorpAction
        rows with impossible dec > ex."""
        fetcher = _make_fetcher_with_trading(
            payload={"cash_dividends": []},
            announcements=[
                FakeAnnouncement(
                    initiating_symbol="MSFT",
                    ex_date=date(2024, 9, 15),
                    declaration_date=date(2026, 5, 20),  # 20-month-late restatement
                    ca_type="dividend",
                    ca_sub_type="cash",
                    cash=0.75,
                ),
            ],
        )
        # Declaration window for end=2026-05-31 is [2026-04-01, 2026-05-31].
        # The restatement's declaration_date=2026-05-20 IS in this window,
        # so it'd be returned by the announcements endpoint. The v0.9.0
        # filter drops it because dec > ex.
        events = fetcher.get_corp_actions(
            symbols=["MSFT"], start=date(2026, 5, 1), end=date(2026, 5, 31)
        )
        assert events == []  # no phantom restatement row

    def test_legitimate_declared_equals_ex_is_kept(self):
        """Edge case: declared == ex is allowed (some companies declare
        on the morning of ex-date for technical timing). Only strict
        declared > ex is dropped."""
        fetcher = _make_fetcher_with_trading(
            payload={
                "cash_dividends": [
                    FakeDividend(
                        symbol="KO",
                        rate=0.44,
                        ex_date=date(2022, 3, 14),
                        process_date=date(2022, 3, 15),
                    )
                ]
            },
            announcements=[
                FakeAnnouncement(
                    initiating_symbol="KO",
                    ex_date=date(2022, 3, 14),
                    declaration_date=date(2022, 3, 14),  # dec == ex; kept
                ),
            ],
        )
        events = fetcher.get_corp_actions(
            symbols=["KO"], start=date(2022, 3, 1), end=date(2022, 4, 1)
        )
        assert len(events) == 1
        assert events[0].declared_date == date(2022, 3, 14)
