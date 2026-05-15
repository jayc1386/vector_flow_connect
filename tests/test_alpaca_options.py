"""Tests for AlpacaOptionsFetcher + `fetch_chain_bars` against stubs."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest

from vector_flow_connect.alpaca.occ import generate_occ_symbol
from vector_flow_connect.alpaca.options import (
    AlpacaOptionsFetcher,
    ChainBarsResult,
    FetchedOptionBar,
    FetchedOptionContract,
    fetch_chain_bars,
)
from vector_flow_connect.alpaca.settings import AlpacaCredentials


@dataclass
class FakeOptionBar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    trade_count: int | None = None
    vwap: float | None = None


@dataclass
class FakeOptionBarSet:
    data: dict[str, list[FakeOptionBar]]


class FakeOptionsClient:
    def __init__(
        self,
        chain: dict[str, Any] | None = None,
        bars_by_symbol: dict[str, list[FakeOptionBar]] | None = None,
    ) -> None:
        self._chain = chain or {}
        self._bars_by_symbol = bars_by_symbol or {}
        self.bar_call_count = 0
        self.bar_call_log: list[list[str]] = field(default_factory=list)  # populated below
        self.bar_call_log = []

    def get_option_chain(self, req: Any) -> dict[str, Any]:
        return dict(self._chain)

    def get_option_bars(self, req: Any) -> FakeOptionBarSet:
        self.bar_call_count += 1
        symbols = list(req.symbol_or_symbols)
        self.bar_call_log.append(symbols)
        # Return whatever was scripted for each requested symbol.
        out: dict[str, list[FakeOptionBar]] = {}
        for sym in symbols:
            if sym in self._bars_by_symbol:
                out[sym] = list(self._bars_by_symbol[sym])
        return FakeOptionBarSet(data=out)


def _make_fetcher(
    chain: dict[str, Any] | None = None,
    bars_by_symbol: dict[str, list[FakeOptionBar]] | None = None,
) -> AlpacaOptionsFetcher:
    fetcher = AlpacaOptionsFetcher.from_credentials(
        AlpacaCredentials(api_key="test", secret_key="test", feed="sip")
    )
    fetcher._client = FakeOptionsClient(chain=chain, bars_by_symbol=bars_by_symbol)  # pyright: ignore[reportAttributeAccessIssue]
    return fetcher


class TestAlpacaOptionsFetcherChain:
    def test_get_chain_parses_occ(self):
        # Chain entries keyed by OCC symbol (value irrelevant to the fetcher).
        chain = {
            "SPY240920C00450000": object(),
            "SPY240920P00450000": object(),
        }
        fetcher = _make_fetcher(chain=chain)
        contracts = fetcher.get_chain(underlying="SPY")
        assert len(contracts) == 2
        by_right = {c.right: c for c in contracts}
        assert by_right["C"].strike == Decimal("450")
        assert by_right["C"].expiration_date == date(2024, 9, 20)

    def test_get_chain_skips_malformed(self):
        chain = {
            "SPY240920C00450000": object(),
            "notasymbol": object(),
        }
        fetcher = _make_fetcher(chain=chain)
        contracts = fetcher.get_chain(underlying="SPY")
        assert len(contracts) == 1
        assert contracts[0].symbol == "SPY240920C00450000"


class TestAlpacaOptionsFetcherBars:
    def test_get_bars_pydantic_shape(self):
        occ_sym = "SPY240920C00450000"
        fake = FakeOptionBar(
            timestamp=datetime(2024, 9, 20, tzinfo=UTC),
            open=5.5,
            high=6.0,
            low=5.25,
            close=5.75,
            volume=12345,
            trade_count=42,
            vwap=5.6,
        )
        fetcher = _make_fetcher(bars_by_symbol={occ_sym: [fake]})
        bars = fetcher.get_bars(
            occ_symbols=[occ_sym], start=date(2024, 9, 20), end=date(2024, 9, 20)
        )
        assert len(bars) == 1
        b = bars[0]
        assert b.symbol == occ_sym
        assert b.close == Decimal("5.75")
        assert b.volume == 12345
        assert b.trade_count == 42
        assert b.vwap == Decimal("5.6")

    def test_get_bars_empty_symbols_short_circuits(self):
        fetcher = _make_fetcher()
        bars = fetcher.get_bars(occ_symbols=[], start=date(2024, 9, 20), end=date(2024, 9, 20))
        assert bars == []
        assert fetcher._client.bar_call_count == 0  # pyright: ignore[reportAttributeAccessIssue]


class TestFetchedOptionShapes:
    def test_contract_extra_forbid(self):
        from pydantic import ValidationError

        valid = FetchedOptionContract(
            symbol="SPY240920C00450000",
            expiration_date=date(2024, 9, 20),
            strike=Decimal("450"),
            right="C",
        )
        with pytest.raises(ValidationError):
            FetchedOptionContract.model_validate({**valid.model_dump(), "extra": "x"})

    def test_bar_extra_forbid(self):
        from pydantic import ValidationError

        valid = FetchedOptionBar(
            symbol="SPY240920C00450000",
            timestamp=datetime(2024, 9, 20, tzinfo=UTC),
            open=Decimal("1"),
            high=Decimal("1"),
            low=Decimal("1"),
            close=Decimal("1"),
            volume=1,
        )
        with pytest.raises(ValidationError):
            FetchedOptionBar.model_validate({**valid.model_dump(), "extra": "x"})


class TestFetchChainBars:
    def test_enumerates_grid_and_fetches_bars(self):
        # Window: 2024-09-09 (Mon) through 2024-09-13 (Fri). DTE 0..7 covers
        # the 2024-09-13 Friday only.
        underlying_close = Decimal("450")
        spot_lookup = lambda _d: underlying_close  # noqa: E731

        # Fake bars for one of the enumerated symbols.
        target_sym = generate_occ_symbol("SPY", date(2024, 9, 13), "C", Decimal("450"))
        fake_bar = FakeOptionBar(
            timestamp=datetime(2024, 9, 12, tzinfo=UTC),
            open=5.0,
            high=5.5,
            low=4.9,
            close=5.3,
            volume=100,
        )
        fetcher = _make_fetcher(bars_by_symbol={target_sym: [fake_bar]})

        result = fetch_chain_bars(
            fetcher,
            underlying="SPY",
            start=date(2024, 9, 9),
            end=date(2024, 9, 13),
            spot_lookup=spot_lookup,
            dte_min=0,
            dte_max=7,
            strike_band_pct=Decimal("0.05"),
            rate_limit_sleep_secs=0.0,
        )

        # Sanity: contracts should include the targeted (450, C) symbol.
        assert any(c.symbol == target_sym for c in result.contracts)
        # Each strike has both C and P sides.
        rights = {c.right for c in result.contracts}
        assert rights == {"C", "P"}
        # Bars: only the symbol we scripted bars for came back.
        assert len(result.bars) == 1
        assert result.bars[0].symbol == target_sym
        assert result.rows_seen == 1
        assert result.errors == []

    def test_anchor_miss_skips_expiration_with_error(self):
        # spot_lookup returns None for one specific anchor → that expiration
        # should be skipped, captured in errors. The remaining expiration(s)
        # should still produce contracts.
        def spot_lookup(d: date) -> Decimal | None:
            # Anchor for 2024-09-20 expiration is 30 days prior = 2024-08-21.
            # Anchor for 2024-09-27 is 2024-08-28.
            if d == date(2024, 8, 21):
                return None
            return Decimal("450")

        fetcher = _make_fetcher()

        result = fetch_chain_bars(
            fetcher,
            underlying="SPY",
            start=date(2024, 9, 16),
            end=date(2024, 9, 27),
            spot_lookup=spot_lookup,
            dte_min=0,
            dte_max=11,
            strike_band_pct=Decimal("0.05"),
            anchor_days_before_expiration=30,
            rate_limit_sleep_secs=0.0,
        )

        # One expiration (2024-09-20) skipped with an explicit error.
        assert any("2024-08-21" in e for e in result.errors)
        # The other expiration (2024-09-27) produced contracts.
        assert any(c.expiration_date == date(2024, 9, 27) for c in result.contracts)
        # But none for the skipped expiration.
        assert not any(c.expiration_date == date(2024, 9, 20) for c in result.contracts)

    def test_no_fridays_returns_empty_result(self):
        # Mon..Thu (no Friday in the DTE-windowed range).
        fetcher = _make_fetcher()
        result = fetch_chain_bars(
            fetcher,
            underlying="SPY",
            start=date(2024, 9, 9),
            end=date(2024, 9, 9),
            spot_lookup=lambda _d: Decimal("450"),
            dte_min=0,
            dte_max=3,
            rate_limit_sleep_secs=0.0,
        )
        assert isinstance(result, ChainBarsResult)
        assert result.contracts == []
        assert result.bars == []

    def test_fetch_error_captured_not_raised(self):
        # If the fetcher raises on a batch, fetch_chain_bars should capture
        # the error and continue.
        class BoomClient(FakeOptionsClient):
            def get_option_bars(self, req: Any) -> FakeOptionBarSet:
                raise RuntimeError("rate limited")

        fetcher = AlpacaOptionsFetcher.from_credentials(
            AlpacaCredentials(api_key="t", secret_key="t", feed="sip")
        )
        fetcher._client = BoomClient()  # pyright: ignore[reportAttributeAccessIssue]

        result = fetch_chain_bars(
            fetcher,
            underlying="SPY",
            start=date(2024, 9, 9),
            end=date(2024, 9, 13),
            spot_lookup=lambda _d: Decimal("450"),
            dte_min=0,
            dte_max=7,
            strike_band_pct=Decimal("0.05"),
            rate_limit_sleep_secs=0.0,
        )

        # No bars; error captured.
        assert result.bars == []
        assert any("RuntimeError: rate limited" in e for e in result.errors)
        # Contracts still enumerated.
        assert len(result.contracts) > 0

    def test_batches_under_size_cap(self):
        # Tiny batch_size forces multiple fetcher calls; verify batching.
        # Range with multiple strikes produces enough contracts to batch.
        fetcher = _make_fetcher()

        fetch_chain_bars(
            fetcher,
            underlying="SPY",
            start=date(2024, 9, 9),
            end=date(2024, 9, 13),
            spot_lookup=lambda _d: Decimal("450"),
            dte_min=0,
            dte_max=7,
            strike_band_pct=Decimal("0.10"),  # ±10% x $1 spacing x 2 rights = ~182 contracts
            batch_size=50,
            rate_limit_sleep_secs=0.0,
        )

        # 91 strikes x 2 rights = 182 contracts. With batch_size=50, that's
        # 4 calls (50, 50, 50, 32).
        client: FakeOptionsClient = fetcher._client  # type: ignore[assignment]
        assert client.bar_call_count >= 4
        # Each batch within size cap.
        assert all(len(batch) <= 50 for batch in client.bar_call_log)
