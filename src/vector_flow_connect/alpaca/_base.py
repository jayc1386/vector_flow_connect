"""Fetcher Protocols (vendor-neutral contracts).

These describe what a vendor fetcher provides at the read-side
boundary. Concrete implementations live alongside in
`vector_flow_connect.alpaca.{bars,options,corp_actions}`. Consumers
can swap fakes for tests by satisfying the Protocol.

Located under `alpaca/` for v0 since only Alpaca implementations
exist. When a second vendor lands, promote these Protocols to the
package root (`vector_flow_connect/_base.py`).
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from vector_flow_connect.alpaca.bars import FetchedBar
    from vector_flow_connect.alpaca.corp_actions import FetchedCorpAction
    from vector_flow_connect.alpaca.options import (
        FetchedOptionBar,
        FetchedOptionContract,
    )


class BarFetcher(Protocol):
    """Pure read-side contract: fetch bars for symbols x date range.

    Concrete implementations:
    - `AlpacaBarFetcher` — wraps alpaca-py's historical-bars endpoint.
    - Test-only fakes — return canned bars from a dict.

    Future: `PolygonBarFetcher`, etc.
    """

    def get_bars(
        self,
        *,
        symbols: list[str],
        start: date,
        end: date,
    ) -> list[FetchedBar]:
        """Return bars for the requested symbols x [start, end] window.

        Implementations should:
        - Return Alpaca's `adjustment='all'` (split-adjusted) flavor;
          callers write into their canonical `adjusted_close` column.
        - Return an empty list on zero bars; never raise on "no data."
        - Raise on auth / rate-limit / network failure so the caller
          can mark the corresponding ingest request as `failed`.
        """
        ...


class OptionsFetcher(Protocol):
    """Read-side contract: enumerate chain + fetch bars by OCC symbol.

    Concrete implementations:
    - `AlpacaOptionsFetcher` — wraps alpaca-py's `OptionHistoricalDataClient`.
    - Test-only fakes — return canned chain entries + bars.
    """

    def get_chain(self, *, underlying: str) -> list[FetchedOptionContract]:
        """Enumerate the live option chain for `underlying`."""
        ...

    def get_bars(
        self,
        *,
        occ_symbols: list[str],
        start: date,
        end: date,
    ) -> list[FetchedOptionBar]:
        """Fetch daily bars for an OCC-symbol batch over [start, end]."""
        ...


class CorpActionsFetcher(Protocol):
    """Pure read-side contract: fetch corp actions for symbols x range.

    Concrete implementations:
    - `AlpacaCorpActionsFetcher` — wraps alpaca-py's corp-actions endpoint.
    - Test-only fakes — return canned events.

    Future: `PolygonCorpActionsFetcher` when a second source lands.
    """

    def get_corp_actions(
        self,
        *,
        symbols: list[str],
        start: date,
        end: date,
    ) -> list[FetchedCorpAction]:
        """Return v1 events (dividends + splits) for the requested
        symbols x [start, end] window. Implementations should:
        - Skip non-v1 event types at the source boundary.
        - Normalize CUSIP empty-string to None.
        - Return empty list on zero events; never raise on "no data."
        - Raise on auth / rate-limit / network failure so the caller
          can mark the corresponding ingest request as `failed`.
        """
        ...
