"""Polygon (Massive) vendor connectors.

Second vendor after `vector_flow_connect.alpaca`. Fetchers structurally
satisfy the `CorpActionsFetcher` / `BarFetcher` Protocols defined in
`alpaca/_base.py` (those Protocols + the `Fetched*` shared shapes are
slated to promote to the package root now that a second vendor exists —
see the note in `alpaca/_base.py`).
"""

from vector_flow_connect.polygon.bars import PolygonBarFetcher
from vector_flow_connect.polygon.corp_actions import PolygonCorpActionsFetcher
from vector_flow_connect.polygon.settings import PolygonCredentials

__all__ = [
    "PolygonBarFetcher",
    "PolygonCorpActionsFetcher",
    "PolygonCredentials",
]
