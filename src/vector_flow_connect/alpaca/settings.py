"""Vendor-neutral Alpaca credentials shape.

Consumers map their local settings (prism's `prism.config.Settings`,
quant_hive's `AlpacaSettings`) → `AlpacaCredentials` at fetcher
construction time. The connectors package itself does no env-var
loading.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class AlpacaCredentials(BaseModel):
    """Alpaca API credentials.

    `feed` is bars-only (Alpaca's options + corporate-actions clients
    don't take a feed selector). Default `"sip"` matches prism's
    historical setting.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    api_key: str
    secret_key: str
    feed: str = "sip"
