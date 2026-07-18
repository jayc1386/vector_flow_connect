"""Alpaca vendor connectors.

Layout is flat for v0; promotes to `vendors/alpaca/` when a second
vendor (polygon, IEX) lands.
"""

from vector_flow_connect.alpaca._base import (
    BarFetcher,
    CorpActionsFetcher,
    NewsFetcher,
    OptionsFetcher,
    PositionsFetcher,
)
from vector_flow_connect.alpaca.bars import AlpacaBarFetcher, FetchedBar
from vector_flow_connect.alpaca.corp_actions import (
    AlpacaCorpActionsFetcher,
    FetchedCorpAction,
)
from vector_flow_connect.alpaca.news import AlpacaNewsFetcher, FetchedNewsArticle
from vector_flow_connect.alpaca.occ import (
    friday_expirations,
    generate_occ_symbol,
    parse_occ_symbol,
    strikes_in_band,
)
from vector_flow_connect.alpaca.options import (
    AlpacaOptionsFetcher,
    ChainBarsResult,
    FetchedOptionBar,
    FetchedOptionContract,
    fetch_chain_bars,
)
from vector_flow_connect.alpaca.positions import (
    AlpacaPositionsFetcher,
    FetchedPosition,
)
from vector_flow_connect.alpaca.settings import (
    AlpacaCredentials,
    AlpacaTradingCredentials,
)

__all__ = [
    "AlpacaBarFetcher",
    "AlpacaCorpActionsFetcher",
    "AlpacaCredentials",
    "AlpacaNewsFetcher",
    "AlpacaOptionsFetcher",
    "AlpacaPositionsFetcher",
    "AlpacaTradingCredentials",
    "BarFetcher",
    "ChainBarsResult",
    "CorpActionsFetcher",
    "FetchedBar",
    "FetchedCorpAction",
    "FetchedNewsArticle",
    "FetchedOptionBar",
    "FetchedOptionContract",
    "FetchedPosition",
    "NewsFetcher",
    "OptionsFetcher",
    "PositionsFetcher",
    "fetch_chain_bars",
    "friday_expirations",
    "generate_occ_symbol",
    "parse_occ_symbol",
    "strikes_in_band",
]
