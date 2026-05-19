"""Tests for AlpacaPositionsFetcher against a stubbed alpaca-py TradingClient."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pytest
from pydantic import ValidationError

from vector_flow_connect.alpaca.positions import (
    AlpacaPositionsFetcher,
    FetchedPosition,
)
from vector_flow_connect.alpaca.settings import AlpacaTradingCredentials


@dataclass
class FakePositionSide:
    """Mirrors `alpaca.trading.enums.PositionSide`."""

    value: str


@dataclass
class FakeAssetClass:
    """Mirrors `alpaca.trading.enums.AssetClass`."""

    value: str


@dataclass
class FakePosition:
    """Mirrors `alpaca.trading.models.Position` for the field surface
    the fetcher reads. alpaca-py emits numeric fields as `str`."""

    symbol: str
    qty: str
    side: FakePositionSide
    avg_entry_price: str
    cost_basis: str
    asset_class: FakeAssetClass
    market_value: str | None = None
    unrealized_pl: str | None = None


@dataclass
class FakeAccount:
    """Mirrors `alpaca.trading.models.TradeAccount` for the field
    surface the fetcher reads."""

    account_number: str


class FakeTradingClient:
    """Stub TradingClient. Records call counts so tests can assert
    no extra round-trips happen."""

    def __init__(
        self,
        *,
        positions: list[FakePosition] | None = None,
        account_number: str = "PA3FAKE",
    ) -> None:
        self._positions = positions or []
        self._account = FakeAccount(account_number=account_number)
        self.get_all_positions_calls = 0
        self.get_account_calls = 0

    def get_all_positions(self) -> list[FakePosition]:
        self.get_all_positions_calls += 1
        return list(self._positions)

    def get_account(self) -> FakeAccount:
        self.get_account_calls += 1
        return self._account


def _make_fetcher(
    *,
    positions: list[FakePosition] | None = None,
    account_number: str = "PA3FAKE",
) -> AlpacaPositionsFetcher:
    fetcher = AlpacaPositionsFetcher.from_credentials(
        AlpacaTradingCredentials(api_key="test", secret_key="test", paper=True)
    )
    fetcher._client = FakeTradingClient(  # pyright: ignore[reportAttributeAccessIssue]
        positions=positions, account_number=account_number
    )
    return fetcher


def _long_spy() -> FakePosition:
    return FakePosition(
        symbol="SPY",
        qty="10",
        side=FakePositionSide(value="long"),
        avg_entry_price="500.00",
        cost_basis="5000.00",
        market_value="5300.00",
        unrealized_pl="300.00",
        asset_class=FakeAssetClass(value="us_equity"),
    )


def _short_aapl() -> FakePosition:
    return FakePosition(
        symbol="AAPL",
        qty="-5",
        side=FakePositionSide(value="short"),
        avg_entry_price="180.50",
        cost_basis="-902.50",
        market_value="-885.00",
        unrealized_pl="17.50",
        asset_class=FakeAssetClass(value="us_equity"),
    )


def test_get_positions_normalizes_decimals() -> None:
    fetcher = _make_fetcher(positions=[_long_spy()])
    result = fetcher.get_positions()
    assert len(result) == 1
    pos = result[0]
    assert pos.symbol == "SPY"
    assert pos.qty == Decimal("10")
    assert isinstance(pos.qty, Decimal)
    assert pos.avg_entry_price == Decimal("500.00")
    assert pos.cost_basis == Decimal("5000.00")
    assert pos.market_value == Decimal("5300.00")
    assert pos.unrealized_pl == Decimal("300.00")


def test_get_positions_preserves_side_from_alpaca() -> None:
    fetcher = _make_fetcher(positions=[_long_spy(), _short_aapl()])
    result = fetcher.get_positions()
    by_symbol = {p.symbol: p for p in result}
    assert by_symbol["SPY"].side == "long"
    assert by_symbol["SPY"].qty == Decimal("10")
    assert by_symbol["AAPL"].side == "short"
    assert by_symbol["AAPL"].qty == Decimal("-5")


def test_get_positions_handles_null_market_value() -> None:
    pos = FakePosition(
        symbol="ILLIQUID",
        qty="100",
        side=FakePositionSide(value="long"),
        avg_entry_price="5.00",
        cost_basis="500.00",
        market_value=None,
        unrealized_pl=None,
        asset_class=FakeAssetClass(value="us_equity"),
    )
    fetcher = _make_fetcher(positions=[pos])
    result = fetcher.get_positions()
    assert result[0].market_value is None
    assert result[0].unrealized_pl is None
    assert result[0].cost_basis == Decimal("500.00")


def test_get_positions_carries_asset_class() -> None:
    pos = FakePosition(
        symbol="SPY250620C00500000",
        qty="1",
        side=FakePositionSide(value="long"),
        avg_entry_price="3.50",
        cost_basis="350.00",
        market_value="400.00",
        unrealized_pl="50.00",
        asset_class=FakeAssetClass(value="us_option"),
    )
    fetcher = _make_fetcher(positions=[pos])
    result = fetcher.get_positions()
    assert result[0].asset_class == "us_option"


def test_get_account_number_returns_string() -> None:
    fetcher = _make_fetcher(account_number="PA3ABCDEF")
    assert fetcher.get_account_number() == "PA3ABCDEF"


def test_empty_account_short_circuits() -> None:
    fetcher = _make_fetcher(positions=[])
    assert fetcher.get_positions() == []
    # Still hits the underlying SDK once — no client-side guard.
    assert fetcher._client.get_all_positions_calls == 1  # pyright: ignore[reportAttributeAccessIssue]


def test_get_positions_each_call_hits_sdk() -> None:
    """No internal caching — every call goes to the SDK so the
    consumer's idempotency / smart-delta decisions stay authoritative."""
    fetcher = _make_fetcher(positions=[_long_spy()])
    fetcher.get_positions()
    fetcher.get_positions()
    assert fetcher._client.get_all_positions_calls == 2  # pyright: ignore[reportAttributeAccessIssue]


def test_pydantic_shape_locked() -> None:
    """`extra='forbid'` — adding an unexpected field on the wire
    should break the test loudly so we notice schema drift."""
    valid = FetchedPosition(
        symbol="SPY",
        qty=Decimal("10"),
        side="long",
        avg_entry_price=Decimal("500.00"),
        cost_basis=Decimal("5000.00"),
        market_value=Decimal("5300.00"),
        unrealized_pl=Decimal("300.00"),
        asset_class="us_equity",
    )
    with pytest.raises(ValidationError):
        FetchedPosition.model_validate({**valid.model_dump(), "extra": "x"})


def test_pydantic_side_literal_locked() -> None:
    """`side` is a string literal — anything outside `'long'|'short'`
    must reject so future broker enums get caught at the boundary."""
    with pytest.raises(ValidationError):
        FetchedPosition(
            symbol="SPY",
            qty=Decimal("10"),
            side="LONG",  # pyright: ignore[reportArgumentType]
            avg_entry_price=Decimal("500.00"),
            cost_basis=Decimal("5000.00"),
            asset_class="us_equity",
        )


def test_from_credentials_uses_paper_flag() -> None:
    """`paper=True` (default) doesn't crash; `paper=False` doesn't
    crash. The `TradingClient` constructor accepts either."""
    paper_creds = AlpacaTradingCredentials(api_key="k", secret_key="s", paper=True)
    live_creds = AlpacaTradingCredentials(api_key="k", secret_key="s", paper=False)
    AlpacaPositionsFetcher.from_credentials(paper_creds)
    AlpacaPositionsFetcher.from_credentials(live_creds)
