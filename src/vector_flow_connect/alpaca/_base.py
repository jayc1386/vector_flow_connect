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

from datetime import date, datetime
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from vector_flow_connect.alpaca.bars import FetchedBar
    from vector_flow_connect.alpaca.corp_actions import FetchedCorpAction
    from vector_flow_connect.alpaca.news import FetchedNewsArticle
    from vector_flow_connect.alpaca.options import (
        FetchedOptionBar,
        FetchedOptionContract,
    )
    from vector_flow_connect.alpaca.positions import FetchedPosition


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


class NewsFetcher(Protocol):
    """Pure read-side contract: drain news articles over a time window.

    Concrete implementations:
    - `AlpacaNewsFetcher` — wraps alpaca-py's `NewsClient`
      (`/v1beta1/news`, Benzinga-sourced).
    - Test-only fakes — return canned articles.
    """

    def get_news(
        self,
        *,
        start: datetime,
        end: datetime,
        symbols: list[str] | None = None,
        include_content: bool = False,
    ) -> list[FetchedNewsArticle]:
        """Return every article in `[start, end]` (inclusive), oldest
        first, de-duplicated on the vendor article id.

        Implementations should:
        - Preserve the article's full `symbols` list even when the
          query filters by symbols.
        - Return empty list on zero articles; never raise on "no data."
        - Raise on auth / rate-limit / network failure so the caller
          can mark the corresponding ingest as `failed`.
        """
        ...


class PositionsFetcher(Protocol):
    """Read-side contract: snapshot the broker's current open positions.

    Concrete implementations:
    - `AlpacaPositionsFetcher` — wraps alpaca-py's `TradingClient`.
    - Test-only fakes — return canned positions + account number.

    Future: `SchwabPositionsFetcher`, `IBKRPositionsFetcher` when
    multi-broker support lands.
    """

    def get_positions(self) -> list[FetchedPosition]:
        """Return the account's current open positions.

        Implementations should:
        - Return empty list when the account has no positions;
          never raise on "no data."
        - Raise on auth / rate-limit / network failure so the caller
          can mark the corresponding ingest as `failed`.
        """
        ...

    def get_account_number(self) -> str:
        """Return the broker's stable account identifier.

        Used by the consumer as the `account_id` write-key on bitemporal
        position tables. Format is broker-specific (Alpaca: `'PA3...'`).
        """
        ...
