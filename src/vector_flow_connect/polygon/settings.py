"""Vendor-neutral Polygon (Massive) credentials shape.

Consumers map their local settings (prism's `prism.config.Settings`) →
`PolygonCredentials` at fetcher construction time. The connectors package
itself does no env-var loading.

Polygon rebranded to "Massive" (Oct 2025); `api.polygon.io` still serves
the REST API (the marketing site 301s to massive.com). A single API key
authenticates every endpoint; the free tier (5 req/min, 2yr history) is
sufficient for recent cross-source corroboration.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class PolygonCredentials(BaseModel):
    """Polygon REST API credentials.

    `base_url` is overridable for testing / future endpoint moves; it
    defaults to the still-live `api.polygon.io` host.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    api_key: str
    base_url: str = "https://api.polygon.io"
