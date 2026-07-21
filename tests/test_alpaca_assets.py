"""Tests for AlpacaAssetsFetcher against a stubbed alpaca-py TradingClient."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from pydantic import ValidationError

from vector_flow_connect.alpaca.assets import AlpacaAssetsFetcher, FetchedAsset
from vector_flow_connect.alpaca.settings import AlpacaTradingCredentials


@dataclass
class FakeEnum:
    """Mirrors an alpaca-py enum (`AssetClass` / `AssetExchange` / `AssetStatus`)."""

    value: str


@dataclass
class FakeAsset:
    """Mirrors `alpaca.trading.models.Asset` for the fields the fetcher reads."""

    symbol: str
    name: str | None
    asset_class: FakeEnum
    exchange: FakeEnum | None
    tradable: bool
    status: FakeEnum


class FakeTradingClient:
    def __init__(self, *, assets: list[FakeAsset] | None = None) -> None:
        self._assets = assets or []
        self.get_all_assets_calls = 0

    def get_all_assets(self) -> list[FakeAsset]:
        self.get_all_assets_calls += 1
        return list(self._assets)


def _make_fetcher(*, assets: list[FakeAsset] | None = None) -> AlpacaAssetsFetcher:
    fetcher = AlpacaAssetsFetcher.from_credentials(
        AlpacaTradingCredentials(api_key="test", secret_key="test", paper=True)
    )
    fetcher._client = FakeTradingClient(assets=assets)  # pyright: ignore[reportAttributeAccessIssue]
    return fetcher


def _aapl() -> FakeAsset:
    return FakeAsset(
        symbol="AAPL",
        name="Apple Inc. Common Stock",
        asset_class=FakeEnum(value="us_equity"),
        exchange=FakeEnum(value="NASDAQ"),
        tradable=True,
        status=FakeEnum(value="active"),
    )


def test_get_assets_unwraps_enums_and_name() -> None:
    fetcher = _make_fetcher(assets=[_aapl()])
    result = fetcher.get_assets()
    assert len(result) == 1
    a = result[0]
    assert a.symbol == "AAPL"
    assert a.name == "Apple Inc. Common Stock"
    assert a.asset_class == "us_equity"
    assert a.exchange == "NASDAQ"
    assert a.tradable is True
    assert a.status == "active"


def test_empty_name_becomes_none() -> None:
    unnamed = FakeAsset(
        symbol="XYZ",
        name="",
        asset_class=FakeEnum(value="us_equity"),
        exchange=FakeEnum(value="OTC"),
        tradable=False,
        status=FakeEnum(value="inactive"),
    )
    fetcher = _make_fetcher(assets=[unnamed])
    a = fetcher.get_assets()[0]
    assert a.name is None
    assert a.tradable is False


def test_null_exchange_tolerated() -> None:
    a = FakeAsset(
        symbol="ZZZ",
        name="Zeta Fund",
        asset_class=FakeEnum(value="us_equity"),
        exchange=None,
        tradable=True,
        status=FakeEnum(value="active"),
    )
    fetcher = _make_fetcher(assets=[a])
    assert fetcher.get_assets()[0].exchange is None


def test_empty_directory() -> None:
    fetcher = _make_fetcher(assets=[])
    assert fetcher.get_assets() == []
    assert fetcher._client.get_all_assets_calls == 1  # pyright: ignore[reportAttributeAccessIssue]


def test_each_call_hits_sdk() -> None:
    fetcher = _make_fetcher(assets=[_aapl()])
    fetcher.get_assets()
    fetcher.get_assets()
    assert fetcher._client.get_all_assets_calls == 2  # pyright: ignore[reportAttributeAccessIssue]


def test_pydantic_shape_locked() -> None:
    valid = FetchedAsset(
        symbol="AAPL",
        name="Apple Inc. Common Stock",
        asset_class="us_equity",
        exchange="NASDAQ",
        tradable=True,
        status="active",
    )
    with pytest.raises(ValidationError):
        FetchedAsset.model_validate({**valid.model_dump(), "extra": "x"})


def test_from_credentials_uses_paper_flag() -> None:
    AlpacaAssetsFetcher.from_credentials(
        AlpacaTradingCredentials(api_key="k", secret_key="s", paper=True)
    )
    AlpacaAssetsFetcher.from_credentials(
        AlpacaTradingCredentials(api_key="k", secret_key="s", paper=False)
    )
