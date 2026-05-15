"""Alpaca options fetcher + `fetch_chain_bars` high-level primitive.

Storage-agnostic. The OCC enumeration path (originally landed in prism
Plan 0023) is exposed as a primitive that takes a `spot_lookup`
callable so the package doesn't import a database. Consumers wire the
callable to whatever bar source they have locally.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from vector_flow_connect.alpaca.occ import (
    friday_expirations,
    generate_occ_symbol,
    parse_occ_symbol,
    strikes_in_band,
)
from vector_flow_connect.alpaca.settings import AlpacaCredentials

DEFAULT_STRIKE_BAND_PCT = Decimal("0.15")
"""Default ±15% of underlying close."""

DEFAULT_DTE_MAX = 90
"""Default upper DTE bound."""

DEFAULT_CONTRACT_BATCH_SIZE = 39
"""Per-call contract cap for the chain-snapshot path. Alpaca's 10K-row
cap / 252 trading days per year ≈ 39 contracts/batch keeps a single
year's fetch under the cap."""

DEFAULT_OCC_BATCH_SIZE = 200
"""Per-call OCC-symbol cap for the enumeration backfill path. Many
enumerated symbols return empty bars (over-enumeration is harmless),
so the call shape is request-limited rather than row-limited and we
can pack more symbols per request than the chain-snapshot path."""

DEFAULT_ANCHOR_DAYS_BEFORE_EXPIRATION = 30
"""Per-expiration strike-band anchor offset. Strikes for an expiration
are centered on the underlying's close N days prior — a coarse proxy
for typical listing-time moneyness. Over-enumeration is free."""

DEFAULT_RATE_LIMIT_SLEEP_SECS = 60.0 / 200.0
"""Free-tier Alpaca options endpoint = 200 req/min."""


class FetchedOptionContract(BaseModel):
    """One option-chain entry as returned by an `OptionsFetcher`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str  # OCC format
    expiration_date: date
    strike: Decimal
    right: str  # 'C' | 'P'


class FetchedOptionBar(BaseModel):
    """One daily option bar as returned by an `OptionsFetcher`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str  # OCC format
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    trade_count: int | None = None
    vwap: Decimal | None = None


@dataclass(frozen=True)
class ChainBarsResult:
    """Result of `fetch_chain_bars`.

    `contracts` are the enumerated `(expiration x strike x C/P)` grid,
    suitable for the consumer to upsert into its contract registry.
    `bars` are the fetched daily bars across those contracts. `errors`
    captures per-expiration anchor-miss + per-batch fetch failures —
    callers decide whether to surface or short-circuit.
    """

    contracts: list[FetchedOptionContract]
    bars: list[FetchedOptionBar]
    errors: list[str] = field(default_factory=list)
    rows_seen: int = 0


class AlpacaOptionsFetcher:
    """Concrete `OptionsFetcher` backed by alpaca-py's
    `OptionHistoricalDataClient`. Constructed via
    `AlpacaOptionsFetcher.from_credentials()`. The `alpaca-py` imports
    happen inside methods so test paths using fakes don't pay the
    import cost.
    """

    def __init__(self, *, api_key: str, api_secret: str) -> None:
        from alpaca.data.historical.option import OptionHistoricalDataClient

        self._client = OptionHistoricalDataClient(api_key, api_secret)

    @classmethod
    def from_credentials(cls, credentials: AlpacaCredentials) -> AlpacaOptionsFetcher:
        return cls(
            api_key=credentials.api_key,
            api_secret=credentials.secret_key,
        )

    def get_chain(self, *, underlying: str) -> list[FetchedOptionContract]:
        from alpaca.data.requests import OptionChainRequest

        req = OptionChainRequest(underlying_symbol=underlying)
        chain = self._client.get_option_chain(req)
        out: list[FetchedOptionContract] = []
        # `chain` is dict[str (OCC symbol), OptionsSnapshot].
        for occ_symbol in chain:  # pyright: ignore[reportGeneralTypeIssues]
            try:
                _, expiration, right, strike = parse_occ_symbol(occ_symbol)
            except ValueError:
                continue
            out.append(
                FetchedOptionContract(
                    symbol=occ_symbol,
                    expiration_date=expiration,
                    strike=strike,
                    right=right,
                )
            )
        return out

    def get_bars(
        self,
        *,
        occ_symbols: list[str],
        start: date,
        end: date,
    ) -> list[FetchedOptionBar]:
        from alpaca.data.requests import OptionBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

        if not occ_symbols:
            return []
        req = OptionBarsRequest(
            symbol_or_symbols=occ_symbols,
            start=datetime(start.year, start.month, start.day, tzinfo=timezone.utc),
            end=datetime(end.year, end.month, end.day, 23, 59, 59, tzinfo=timezone.utc),
            timeframe=TimeFrame(amount=1, unit=TimeFrameUnit.Day),  # pyright: ignore[reportArgumentType]
        )
        barset = self._client.get_option_bars(req)
        out: list[FetchedOptionBar] = []
        for symbol, bars in barset.data.items():  # pyright: ignore[reportAttributeAccessIssue]
            for b in bars:
                out.append(
                    FetchedOptionBar(
                        symbol=symbol,
                        timestamp=b.timestamp,
                        open=Decimal(str(b.open)),
                        high=Decimal(str(b.high)),
                        low=Decimal(str(b.low)),
                        close=Decimal(str(b.close)),
                        volume=int(b.volume),
                        trade_count=int(b.trade_count) if b.trade_count is not None else None,
                        vwap=Decimal(str(b.vwap)) if b.vwap is not None else None,
                    )
                )
        return out


def fetch_chain_bars(
    fetcher: AlpacaOptionsFetcher,
    *,
    underlying: str,
    start: date,
    end: date,
    spot_lookup: Callable[[date], Decimal | None],
    dte_min: int = 0,
    dte_max: int = DEFAULT_DTE_MAX,
    strike_band_pct: Decimal = DEFAULT_STRIKE_BAND_PCT,
    strike_increment: Decimal = Decimal("1"),
    anchor_days_before_expiration: int = DEFAULT_ANCHOR_DAYS_BEFORE_EXPIRATION,
    batch_size: int = DEFAULT_OCC_BATCH_SIZE,
    rate_limit_sleep_secs: float = DEFAULT_RATE_LIMIT_SLEEP_SECS,
) -> ChainBarsResult:
    """OCC enumeration + per-symbol bar fetching, storage-agnostic.

    Enumerates Fridays in `[start + dte_min, end + dte_max]`, anchors
    per-expiration strikes to `spot_lookup(expiration - N days)` within
    `±strike_band_pct`, generates OCC symbols, and fetches bars per
    `batch_size`-wide OCC batch under a `rate_limit_sleep_secs`
    throttle.

    Closes the live-chain-snapshot blind spot (Plan 0023): chain
    snapshots return only currently-listed contracts, so already-
    expired contracts in the target window are unreachable. OCC
    enumeration generates them algorithmically.

    `spot_lookup(anchor_date)` should return the underlying's close
    on or before `anchor_date`, or `None` if no price is available
    (in which case that expiration is skipped with an error captured
    in `ChainBarsResult.errors`).

    Returns the enumerated contracts + fetched bars + per-expiration
    anchor-miss / per-batch fetch errors. The caller is responsible
    for upserting contracts into its registry, mapping OCC symbol →
    contract id when persisting bars, and surfacing or short-circuiting
    on errors.
    """
    errors: list[str] = []
    rows_seen = 0

    # ---- enumerate expirations covering [start + dte_min, end + dte_max] ----
    expiry_window_start = start + timedelta(days=dte_min)
    expiry_window_end = end + timedelta(days=dte_max)
    expirations = friday_expirations(expiry_window_start, expiry_window_end)
    if not expirations:
        return ChainBarsResult(contracts=[], bars=[])

    # ---- per-expiration strike enumeration via prior-N-day underlying close ----
    contracts: list[FetchedOptionContract] = []
    seen_symbols: set[str] = set()
    for exp in expirations:
        anchor_date = exp - timedelta(days=anchor_days_before_expiration)
        close = spot_lookup(anchor_date)
        if close is None:
            errors.append(
                f"no underlying close for {underlying} on or before "
                f"{anchor_date} (anchor for expiration {exp})"
            )
            continue
        strikes = strikes_in_band(close, strike_band_pct, strike_increment)
        for strike in strikes:
            for right in ("C", "P"):
                occ = generate_occ_symbol(underlying, exp, right, strike)
                if occ in seen_symbols:
                    continue
                seen_symbols.add(occ)
                contracts.append(
                    FetchedOptionContract(
                        symbol=occ,
                        expiration_date=exp,
                        strike=strike,
                        right=right,
                    )
                )

    # ---- chunked per-OCC bar fetch under rate-limit throttle ----
    bars: list[FetchedOptionBar] = []
    symbols = [c.symbol for c in contracts]
    for chunk_start, chunk_end in _year_chunks(start, end):
        for symbol_batch in _chunked(symbols, batch_size):
            if rate_limit_sleep_secs > 0:
                time.sleep(rate_limit_sleep_secs)
            try:
                fetched = fetcher.get_bars(
                    occ_symbols=symbol_batch,
                    start=chunk_start,
                    end=chunk_end,
                )
            except Exception as exc:
                errors.append(
                    f"get_bars {chunk_start}..{chunk_end} "
                    f"({len(symbol_batch)} symbols): {type(exc).__name__}: {exc}"
                )
                continue
            rows_seen += len(fetched)
            bars.extend(fetched)

    return ChainBarsResult(
        contracts=contracts,
        bars=bars,
        errors=errors,
        rows_seen=rows_seen,
    )


def _year_chunks(start: date, end: date) -> list[tuple[date, date]]:
    """Split `[start, end]` into per-calendar-year sub-ranges."""
    if end < start:
        return []
    chunks: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        year_end = date(cursor.year, 12, 31)
        chunk_end = year_end if year_end < end else end
        chunks.append((cursor, chunk_end))
        cursor = date(cursor.year + 1, 1, 1)
    return chunks


def _chunked(items: list[str], size: int) -> list[list[str]]:
    """Split `items` into successive lists of at most `size` elements."""
    if size <= 0:
        raise ValueError("size must be positive")
    return [items[i : i + size] for i in range(0, len(items), size)]
