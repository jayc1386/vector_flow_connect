"""Alpaca broker-positions fetcher.

Wraps alpaca-py's `TradingClient.get_all_positions()` + `get_account()`
for the broker-of-record snapshot consumed by prism's reconciliation
engine. Pure read-side; consumers write canonical state.

`get_all_positions()` returns the full account snapshot in one
response — no chunking, no pagination. The paper-tier endpoint and
the live-tier endpoint share the same shape; `AlpacaTradingCredentials.paper`
selects which.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from vector_flow_connect.alpaca.settings import AlpacaTradingCredentials


class FetchedPosition(BaseModel):
    """One open position as returned by a `PositionsFetcher`.
    Vendor-agnostic shape; concrete fetchers normalize per-vendor
    Position models into this single shape.

    Signed fields (`qty`, `market_value`, `cost_basis`) match the
    broker's convention: positive for long, negative for short. The
    `side` field is the broker's own classification — trust it over
    qty sign in case of edge cases (e.g., closed-but-not-cleared
    positions where qty could transiently be zero).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    qty: Decimal
    side: Literal["long", "short"]
    avg_entry_price: Decimal
    market_value: Decimal | None = None
    cost_basis: Decimal
    unrealized_pl: Decimal | None = None
    asset_class: str


class AlpacaPositionsFetcher:
    """Concrete `PositionsFetcher` backed by alpaca-py's `TradingClient`.

    Constructed via `AlpacaPositionsFetcher.from_credentials()`. The
    `alpaca-py` import happens inside the constructor so unit tests
    using fakes don't pay the import cost or require live env vars.

    Same `paper=True` / `paper=False` flag as the corp-actions sidecar:
    paper endpoint serves identical position data for a paper account;
    live endpoint serves a live account. Pick at construction time.
    """

    def __init__(self, *, api_key: str, api_secret: str, paper: bool = True) -> None:
        from alpaca.trading.client import TradingClient

        self._client: Any = TradingClient(api_key, api_secret, paper=paper)

    @classmethod
    def from_credentials(cls, credentials: AlpacaTradingCredentials) -> AlpacaPositionsFetcher:
        return cls(
            api_key=credentials.api_key,
            api_secret=credentials.secret_key,
            paper=credentials.paper,
        )

    def get_positions(self) -> list[FetchedPosition]:
        """Return the account's current open positions.

        Implementations should:
        - Return empty list when the account has no positions;
          never raise on "no data."
        - Raise on auth / rate-limit / network failure so the caller
          can mark the corresponding ingest as `failed`.
        """
        positions = self._client.get_all_positions()
        return [_position_from_alpaca(p) for p in positions]

    def get_account_number(self) -> str:
        """Return the broker's account identifier (e.g., `'PA3ABCDEF'`).

        Used by the consumer as the `account_id` write-key on bitemporal
        position tables. Stable for the lifetime of the account.
        """
        return str(self._client.get_account().account_number)


def _position_from_alpaca(p: Any) -> FetchedPosition:
    """Map alpaca-py's `Position` model into a `FetchedPosition`.

    alpaca-py stores numeric fields as `str` (pre-coerced at the SDK
    boundary); `Decimal(str(value))` is defensive against any future
    SDK change to a float type.
    """
    side_value = p.side.value if hasattr(p.side, "value") else str(p.side)
    side: Literal["long", "short"] = "long" if side_value == "long" else "short"
    asset_class = p.asset_class.value if hasattr(p.asset_class, "value") else str(p.asset_class)
    return FetchedPosition(
        symbol=p.symbol,
        qty=Decimal(str(p.qty)),
        side=side,
        avg_entry_price=Decimal(str(p.avg_entry_price)),
        market_value=Decimal(str(p.market_value)) if p.market_value is not None else None,
        cost_basis=Decimal(str(p.cost_basis)),
        unrealized_pl=Decimal(str(p.unrealized_pl)) if p.unrealized_pl is not None else None,
        asset_class=asset_class,
    )
