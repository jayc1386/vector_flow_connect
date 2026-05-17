"""Vendor-neutral Alpaca credentials shapes.

Consumers map their local settings (prism's `prism.config.Settings`,
quant_hive's `AlpacaSettings`) → `AlpacaCredentials` (and optionally
`AlpacaTradingCredentials`) at fetcher construction time. The
connectors package itself does no env-var loading.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class AlpacaCredentials(BaseModel):
    """Alpaca market-data API credentials.

    `feed` is bars-only (Alpaca's options + corporate-actions clients
    don't take a feed selector). Default `"sip"` matches prism's
    historical setting.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    api_key: str
    secret_key: str
    feed: str = "sip"


class AlpacaTradingCredentials(BaseModel):
    """Alpaca trading-API credentials.

    Optional augmentation for `AlpacaCorpActionsFetcher` to source
    `declared_date` from the (deprecated) trading-API
    `/v2/corporate_actions/announcements` endpoint — the only Alpaca
    surface that exposes the announcement date as of 2026-05.

    Alpaca emits a DeprecationWarning on the underlying SDK method
    pointing users to the market-data endpoint, but that migration is
    incomplete (the market-data endpoint dropped declaration_date).
    Until Alpaca resolves the regression, this fetcher accepts the
    deprecation risk to capture declaration_date while available;
    historical values written to canonical storage remain valid
    regardless of future endpoint changes.

    `paper=True` (default) targets `paper-api.alpaca.markets`;
    `paper=False` targets the live trading API. The announcements
    data is identical between the two — paper credentials work for
    universe-scoped read-only corp-actions queries.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    api_key: str
    secret_key: str
    paper: bool = True
