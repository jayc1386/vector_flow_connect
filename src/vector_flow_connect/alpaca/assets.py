"""Alpaca asset-reference fetcher.

Wraps alpaca-py's `TradingClient.get_all_assets()` — the tradable-asset
directory that carries each symbol's human-readable `name` (e.g.
`AAPL` → `"Apple Inc. Common Stock"`). Pure read-side reference data;
consumers match on `symbol` and write canonical `securities.name`.

`get_all_assets()` returns the full directory in one response — no
pagination. The paper endpoint and the live endpoint serve identical
reference data; `AlpacaTradingCredentials.paper` selects which.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from vector_flow_connect.alpaca.settings import AlpacaTradingCredentials


class FetchedAsset(BaseModel):
    """One row of Alpaca's asset directory.

    `name` is the human-readable security name and is the reason this
    fetcher exists; it is `None` for the rare asset Alpaca has not
    named. `asset_class` / `exchange` / `status` are the vendor enum
    string values (e.g. `"us_equity"`, `"NASDAQ"`, `"active"`).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    name: str | None = None
    asset_class: str
    exchange: str | None = None
    tradable: bool
    status: str


class AlpacaAssetsFetcher:
    """Concrete `AssetsFetcher` backed by alpaca-py's `TradingClient`.

    Constructed via `AlpacaAssetsFetcher.from_credentials()`. The
    `alpaca-py` import happens inside the constructor so unit tests
    using fakes don't pay the import cost or require live env vars.

    Same `paper=True` / `paper=False` flag as the positions fetcher:
    the asset directory is identical between the two endpoints.
    """

    def __init__(self, *, api_key: str, api_secret: str, paper: bool = True) -> None:
        from alpaca.trading.client import TradingClient

        from vector_flow_connect.alpaca._session import disable_env_proxies

        self._client: Any = disable_env_proxies(TradingClient(api_key, api_secret, paper=paper))

    @classmethod
    def from_credentials(cls, credentials: AlpacaTradingCredentials) -> AlpacaAssetsFetcher:
        return cls(
            api_key=credentials.api_key,
            api_secret=credentials.secret_key,
            paper=credentials.paper,
        )

    def get_assets(self) -> list[FetchedAsset]:
        """Return the full asset directory in one call.

        Implementations should:
        - Return the complete list; the directory is not paginated.
        - Raise on auth / rate-limit / network failure so the caller
          can mark the corresponding ingest as `failed`.
        """
        return [_asset_from_alpaca(a) for a in self._client.get_all_assets()]


def _asset_from_alpaca(a: Any) -> FetchedAsset:
    """Map alpaca-py's `Asset` model into a `FetchedAsset`.

    `asset_class` / `exchange` / `status` arrive as alpaca-py enums;
    `.value` unwraps them defensively (str fallback if a future SDK
    change hands us a plain string)."""

    def _val(v: Any) -> Any:
        return v.value if hasattr(v, "value") else v

    return FetchedAsset(
        symbol=a.symbol,
        name=a.name or None,
        asset_class=str(_val(a.asset_class)),
        exchange=str(_val(a.exchange)) if a.exchange is not None else None,
        tradable=bool(a.tradable),
        status=str(_val(a.status)),
    )
