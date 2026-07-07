"""Alpaca corporate-actions fetcher.

v1 event types: `cash_dividend`, `forward_split`, `reverse_split`.
Other Alpaca types (UNIT_SPLIT, STOCK_DIVIDEND, SPIN_OFF, *_MERGER,
REDEMPTION, NAME_CHANGE, WORTHLESS_REMOVAL, RIGHTS_DISTRIBUTION) are
skipped at the boundary so the fetcher survives "this universe happens
to have a spinoff this quarter."

Field mapping verified against Alpaca's live API (2026-05-14):
- `cash_dividends` → `event_type='dividend'`, `rate` → `cash_amount`.
- `forward_splits` / `reverse_splits` → `event_type='split'`,
  `old_rate` → `split_ratio_from`, `new_rate` → `split_ratio_to`.
  Direction: forward = `to > from`; reverse = `from > to`.
- `cusip == ""` → None (Alpaca returns empty string for old events
  whose CUSIP wasn't captured).
- ReverseSplit carries `old_cusip` + `new_cusip` separately (a
  symbol-renaming event); both are preserved.

`declared_date` augmentation (v0.2.0, verified 2026-05-17):
- The market-data `/v1/corporate-actions` endpoint does NOT carry
  `declaration_date` on cash_dividend events; the field is sourced
  via a sidecar call to the trading-API
  `/v2/corporate_actions/announcements` endpoint (deprecated by
  alpaca-py but actively populating data — Alpaca regression
  unresolved as of 2026-05). When `AlpacaTradingCredentials` is
  provided, the fetcher pulls universe-wide announcements per
  ≤90-day chunk and joins on `(initiating_symbol, ex_date)`. When
  trading credentials are absent, `declared_date` stays None on every
  event — the v0.1.x behavior, unchanged.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from vector_flow_connect.alpaca.settings import (
    AlpacaCredentials,
    AlpacaTradingCredentials,
)

# Alpaca → prism event_type mapping. Mirrors the verified live API:
# response payloads carry plural keys (`cash_dividends`,
# `forward_splits`, `reverse_splits`).
_DIVIDEND_KEYS = {"cash_dividends", "cash_dividend"}
_FORWARD_SPLIT_KEYS = {"forward_splits", "forward_split"}
_REVERSE_SPLIT_KEYS = {"reverse_splits", "reverse_split"}

# Announcements endpoint enforces a 90-day window cap per call.
_ANNOUNCEMENTS_CHUNK_DAYS = 90

# Declaration-date pass lookback (v0.8.0). The DECLARATION_DATE-filtered
# announcements query covers `[end - _DECLARATION_LOOKBACK_DAYS, end]`,
# catching recent declarations with potentially-future ex_dates that the
# EX_DATE-filtered market-data endpoint won't return until ex_date
# arrives. 60 days covers the typical 14-30d announcement→ex window
# with margin.
_DECLARATION_LOOKBACK_DAYS = 60


class FetchedCorpAction(BaseModel):
    """One corp-action event as returned by a `CorpActionsFetcher`.
    Vendor-agnostic shape; concrete fetchers normalize Alpaca's
    type-specific event models into this single shape."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    event_type: Literal["dividend", "split"]
    ex_date: date
    process_date: date
    declared_date: date | None = None
    record_date: date | None = None
    payable_date: date | None = None
    cash_amount: Decimal | None = None  # dividends only
    split_ratio_from: Decimal | None = None  # splits only
    split_ratio_to: Decimal | None = None  # splits only
    cusip: str | None = None
    new_cusip: str | None = None  # ReverseSplit only
    external_id: str | None = None  # vendor UUID
    is_special: bool | None = None  # dividends only
    is_foreign: bool | None = None  # dividends only


class AlpacaCorpActionsFetcher:
    """Concrete `CorpActionsFetcher` backed by alpaca-py's
    `CorporateActionsClient`. Constructed via
    `AlpacaCorpActionsFetcher.from_credentials()`. The `alpaca-py`
    imports happen inside the constructor so unit tests using fakes
    don't pay the import cost or require live env vars.

    When `trading_api_key` + `trading_api_secret` are provided, the
    fetcher also constructs a `TradingClient` and uses it to source
    `declared_date` from the (deprecated) announcements endpoint.
    When omitted, `declared_date` is left None on every event.
    """

    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        trading_api_key: str | None = None,
        trading_api_secret: str | None = None,
        trading_paper: bool = True,
    ) -> None:
        from alpaca.data.historical.corporate_actions import CorporateActionsClient

        from vector_flow_connect.alpaca._session import disable_env_proxies

        self._client = disable_env_proxies(CorporateActionsClient(api_key, api_secret))
        self._trading_client: Any | None = None
        if trading_api_key and trading_api_secret:
            from alpaca.trading.client import TradingClient

            self._trading_client = disable_env_proxies(
                TradingClient(trading_api_key, trading_api_secret, paper=trading_paper)
            )

    @classmethod
    def from_credentials(
        cls,
        credentials: AlpacaCredentials,
        *,
        trading_credentials: AlpacaTradingCredentials | None = None,
    ) -> AlpacaCorpActionsFetcher:
        if trading_credentials is None:
            return cls(
                api_key=credentials.api_key,
                api_secret=credentials.secret_key,
            )
        return cls(
            api_key=credentials.api_key,
            api_secret=credentials.secret_key,
            trading_api_key=trading_credentials.api_key,
            trading_api_secret=trading_credentials.secret_key,
            trading_paper=trading_credentials.paper,
        )

    def get_corp_actions(
        self,
        *,
        symbols: list[str],
        start: date,
        end: date,
    ) -> list[FetchedCorpAction]:
        """Fetch corp actions over `[start, end]` (ex_date filter on the
        market-data endpoint) and merge with recent declarations from the
        trading-API announcements endpoint (DECLARATION_DATE filter, v0.8.0).

        Two passes when trading credentials are configured:
        1. Market-data endpoint with `start`/`end` ex_date filter →
           historical events plus declared_date augmentation via the
           existing EX_DATE-filtered sidecar.
        2. Announcements endpoint with DECLARATION_DATE filter over
           `[end - _DECLARATION_LOOKBACK_DAYS, end]` → recent
           declarations regardless of ex_date. Catches future-ex events
           that the market-data endpoint won't return until ex_date
           arrives.

        Merge policy: market-data events win on `(symbol, ex_date)`
        collisions (more complete field surface). The announcements
        pass fills `declared_date` when the market-data row's is None
        (catches cap-truncation in the EX_DATE sidecar). Future-ex
        events only present in the announcements pass land as
        first-class rows.
        """
        from alpaca.data.enums import CorporateActionsType
        from alpaca.data.requests import CorporateActionsRequest

        if not symbols:
            return []
        req = CorporateActionsRequest(
            symbols=symbols,
            types=[
                CorporateActionsType.CASH_DIVIDEND,
                CorporateActionsType.FORWARD_SPLIT,
                CorporateActionsType.REVERSE_SPLIT,
            ],
            start=start,
            end=end,
            # v0.10.0: `limit=None` drains every page. alpaca-py's
            # `CorporateActionsRequest.limit` defaults to 1000, and its
            # `_get_marketdata` loop honors that default — it paginates
            # via `next_page_token` at 1000/page but stops once
            # `total_items >= limit`, so the default silently truncates a
            # wide query at the first 1000 events (ASC by date) even
            # though more pages exist. `to_request_fields()` strips
            # None-valued fields (`model_dump(exclude_none=True)`), so
            # `limit=None` omits the param entirely and lets the loop run
            # until `next_page_token is None`. Without it, universe-wide
            # multi-year backfills lose every event past the first 1000.
            limit=None,
        )
        result = self._client.get_corporate_actions(req)

        # Sidecar: pull declared_date from the announcements endpoint
        # (universe-wide; the in-memory lookup is tiny and avoids
        # per-symbol N+1 fanout). Only when trading creds were
        # provided at construction.
        declared_dates: dict[tuple[str, date], date] = {}
        if self._trading_client is not None:
            declared_dates = self._fetch_declared_dates(start=start, end=end)

        out: list[FetchedCorpAction] = []
        for ca_type_key, events in result.data.items():  # pyright: ignore[reportAttributeAccessIssue]
            event_type = _classify_alpaca_type(ca_type_key)
            if event_type is None:
                continue
            for e in events:
                fetched = _normalize_alpaca_event(e, event_type, declared_dates=declared_dates)
                if fetched is not None:
                    out.append(fetched)

        # v0.8.0: second pass for recent declarations (future-ex events).
        # Only runs when trading credentials are configured (same gate as
        # the existing sidecar).
        if self._trading_client is not None:
            announcement_events = self._fetch_recent_announcement_events(
                symbols=set(symbols),
                since=end - timedelta(days=_DECLARATION_LOOKBACK_DAYS),
                until=end,
            )
            out = _merge_announcement_events(out, announcement_events)
        return out

    def _fetch_recent_announcement_events(
        self,
        *,
        symbols: set[str],
        since: date,
        until: date,
    ) -> list[FetchedCorpAction]:
        """v0.8.0 — DECLARATION_DATE pass.

        Query the trading-API announcements endpoint with
        `date_type=CorporateActionDateType.DECLARATION_DATE` over
        `[since, until]`. Returns full `FetchedCorpAction` events
        (dividends + forward/reverse splits) for symbols in `symbols`.

        The endpoint is universe-wide (no multi-symbol filter on the
        request shape); we filter to `symbols` in Python. Chunked at
        ≤90 day boundaries to respect the endpoint's documented cap,
        same shape as `_fetch_declared_dates`.

        Stock-dividend and merger/spinoff sub-types are skipped —
        out of v1 scope (matches the market-data endpoint's filter).
        """
        from alpaca.trading.enums import (
            CorporateActionDateType,
            CorporateActionType,
        )
        from alpaca.trading.requests import GetCorporateAnnouncementsRequest

        assert self._trading_client is not None

        out: list[FetchedCorpAction] = []
        chunk_size = timedelta(days=_ANNOUNCEMENTS_CHUNK_DAYS - 1)  # since/until inclusive
        cursor = since
        while cursor <= until:
            chunk_end = min(cursor + chunk_size, until)
            req = GetCorporateAnnouncementsRequest(
                ca_types=[
                    CorporateActionType.DIVIDEND,
                    CorporateActionType.SPLIT,
                ],
                since=cursor,
                until=chunk_end,
                date_type=CorporateActionDateType.DECLARATION_DATE,
            )
            results = self._trading_client.get_corporate_announcements(req)
            for r in results:
                normalized = _normalize_announcement(r)
                if normalized is None:
                    continue
                if normalized.symbol not in symbols:
                    continue
                # v0.9.0: reject restatement records with impossible
                # `declaration_date > ex_date`. Alpaca's deprecated
                # announcements endpoint encodes some retroactive
                # restatement records with `declaration_date =
                # (when-the-restatement-was-issued)` rather than the
                # original board-vote date, producing temporally-
                # incoherent rows (e.g. ex=2020-03-30, declared=2021-04-20).
                # Filter them out so we don't write known-wrong
                # declared_date values downstream.
                if (
                    normalized.declared_date is not None
                    and normalized.declared_date > normalized.ex_date
                ):
                    continue
                out.append(normalized)
            cursor = chunk_end + timedelta(days=1)
        return out

    def _fetch_declared_dates(self, *, start: date, end: date) -> dict[tuple[str, date], date]:
        """Build a `(symbol, ex_date) -> declared_date` lookup by
        chunking the [start, end] range at ≤90-day boundaries (the
        announcements endpoint's documented cap) and pulling
        universe-wide dividend + split announcements per chunk.

        Empty `initiating_symbol` rows (stock dividends keyed on
        `target_symbol`) are skipped — our v1 doesn't ingest stock
        dividends from the market-data endpoint anyway, so there's
        no row to augment.
        """
        from alpaca.trading.enums import (
            CorporateActionDateType,
            CorporateActionType,
        )
        from alpaca.trading.requests import GetCorporateAnnouncementsRequest

        assert self._trading_client is not None

        lookup: dict[tuple[str, date], date] = {}
        chunk_size = timedelta(days=_ANNOUNCEMENTS_CHUNK_DAYS - 1)  # since/until inclusive
        cursor = start
        while cursor <= end:
            chunk_end = min(cursor + chunk_size, end)
            req = GetCorporateAnnouncementsRequest(
                ca_types=[
                    CorporateActionType.DIVIDEND,
                    CorporateActionType.SPLIT,
                ],
                since=cursor,
                until=chunk_end,
                date_type=CorporateActionDateType.EX_DATE,
            )
            results = self._trading_client.get_corporate_announcements(req)
            for r in results:
                sym = getattr(r, "initiating_symbol", None) or None
                ex = getattr(r, "ex_date", None)
                dec = getattr(r, "declaration_date", None)
                if not sym or ex is None or dec is None:
                    continue
                # v0.9.0: reject restatement records with impossible
                # `declaration_date > ex_date` — see the matching
                # filter in `_fetch_recent_announcement_events` for
                # the diagnosis. Same Alpaca data quirk.
                if dec > ex:
                    continue
                lookup[(sym, ex)] = dec
            cursor = chunk_end + timedelta(days=1)
        return lookup


def _normalize_announcement(announcement: Any) -> FetchedCorpAction | None:
    """v0.8.0 — convert a `CorporateActionAnnouncement` from the
    trading-API announcements endpoint into our vendor-agnostic
    `FetchedCorpAction` shape.

    Returns None when:
    - ex_date is missing (cannot key into smart-delta)
    - ca_type is not dividend/split (out of v1 scope)
    - sub_type is stock/unit (stock dividends + unit splits — out of
      v1 scope; consistent with `get_corp_actions` market-data filter)
    """
    try:
        symbol = getattr(announcement, "initiating_symbol", None)
        ex_date_val = getattr(announcement, "ex_date", None)
        if not symbol or ex_date_val is None:
            return None

        ca_type_raw = getattr(announcement, "ca_type", None)
        ca_type_str = getattr(ca_type_raw, "value", str(ca_type_raw)).lower()
        ca_sub_raw = getattr(announcement, "ca_sub_type", None)
        ca_sub_str = getattr(ca_sub_raw, "value", str(ca_sub_raw)).lower()

        if ca_type_str == "dividend":
            if ca_sub_str != "cash":
                # stock dividends out of scope
                return None
            event_type: Literal["dividend", "split"] = "dividend"
        elif ca_type_str == "split":
            if ca_sub_str in {"unit_split", "recapitalization"}:
                return None
            event_type = "split"
        else:
            return None

        declared_date_val = getattr(announcement, "declaration_date", None)
        record_date_val = getattr(announcement, "record_date", None)
        payable_date_val = getattr(announcement, "payable_date", None)

        external_id_raw = getattr(announcement, "id", None)
        external_id = str(external_id_raw) if external_id_raw is not None else None

        cusip_raw = getattr(announcement, "initiating_original_cusip", None)
        cusip = cusip_raw if cusip_raw else None

        if event_type == "dividend":
            cash = getattr(announcement, "cash", None)
            if cash is None:
                return None
            return FetchedCorpAction(
                symbol=str(symbol),
                event_type="dividend",
                ex_date=ex_date_val,
                process_date=ex_date_val,  # announcements endpoint has no process_date; default to ex_date
                declared_date=declared_date_val,
                record_date=record_date_val,
                payable_date=payable_date_val,
                cash_amount=Decimal(str(cash)),
                cusip=cusip,
                external_id=external_id,
            )

        # split
        old_rate = getattr(announcement, "old_rate", None)
        new_rate = getattr(announcement, "new_rate", None)
        if old_rate is None or new_rate is None:
            return None
        return FetchedCorpAction(
            symbol=str(symbol),
            event_type="split",
            ex_date=ex_date_val,
            process_date=ex_date_val,
            declared_date=declared_date_val,
            record_date=record_date_val,
            payable_date=payable_date_val,
            split_ratio_from=Decimal(str(old_rate)),
            split_ratio_to=Decimal(str(new_rate)),
            cusip=cusip,
            external_id=external_id,
        )
    except (AttributeError, ValueError, TypeError):
        return None


def _merge_announcement_events(
    market_data_events: list[FetchedCorpAction],
    announcement_events: list[FetchedCorpAction],
) -> list[FetchedCorpAction]:
    """v0.8.0 — merge the two passes on `(symbol, ex_date)`.

    Market-data events win on collisions (more complete field surface
    from the historical endpoint). Announcement-pass fills
    `declared_date` when the market-data row's is None — catches
    cap-truncation in the EX_DATE-filtered sidecar. Future-ex events
    only present in the announcements pass land as first-class rows.
    """
    by_key: dict[tuple[str, date], FetchedCorpAction] = {
        (e.symbol, e.ex_date): e for e in market_data_events
    }
    for ann in announcement_events:
        key = (ann.symbol, ann.ex_date)
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = ann
        elif existing.declared_date is None and ann.declared_date is not None:
            by_key[key] = existing.model_copy(update={"declared_date": ann.declared_date})
    return list(by_key.values())


def _classify_alpaca_type(alpaca_type_key: str) -> Literal["dividend", "split"] | None:
    if alpaca_type_key in _DIVIDEND_KEYS:
        return "dividend"
    if alpaca_type_key in _FORWARD_SPLIT_KEYS or alpaca_type_key in _REVERSE_SPLIT_KEYS:
        return "split"
    return None


def _normalize_alpaca_event(
    event: Any,
    event_type: Literal["dividend", "split"],
    *,
    declared_dates: dict[tuple[str, date], date] | None = None,
) -> FetchedCorpAction | None:
    """Convert an alpaca-py event object into our vendor-agnostic shape.
    Returns None if the event is malformed in a way we can't recover from.

    `event` is typed as Any because alpaca-py's per-type event models
    (`CashDividend`, `ForwardSplit`, `ReverseSplit`, etc.) share field
    names but no common base class with full type info; we duck-type
    against the documented field surface.

    `declared_dates` is the optional `(symbol, ex_date) -> declared_date`
    lookup built by `AlpacaCorpActionsFetcher._fetch_declared_dates`.
    Empty/absent dict → `declared_date=None` on every event.
    """
    try:
        symbol = str(event.symbol)
        external_id = str(getattr(event, "id", "")) or None
        ex_date_val = event.ex_date
        process_date_val = event.process_date
        record_date_val = getattr(event, "record_date", None)
        payable_date_val = getattr(event, "payable_date", None)
        declared_date_val: date | None = None
        if declared_dates:
            declared_date_val = declared_dates.get((symbol, ex_date_val))

        # Dividends + ForwardSplit have a single `cusip`. ReverseSplit
        # carries `old_cusip` + `new_cusip` separately (a symbol-renaming
        # event). We map the pre-event identity to `cusip` and the
        # post-event identity to `new_cusip` so both are preserved.
        cusip_raw = getattr(event, "cusip", None) or getattr(event, "old_cusip", None)
        cusip = cusip_raw if cusip_raw else None  # empty string -> None
        new_cusip_raw = getattr(event, "new_cusip", None)
        new_cusip = new_cusip_raw if new_cusip_raw else None

        if event_type == "dividend":
            rate = event.rate
            return FetchedCorpAction(
                symbol=symbol,
                event_type="dividend",
                ex_date=ex_date_val,
                process_date=process_date_val,
                declared_date=declared_date_val,
                record_date=record_date_val,
                payable_date=payable_date_val,
                cash_amount=Decimal(str(rate)),
                cusip=cusip,
                external_id=external_id,
                is_special=bool(getattr(event, "special", False)),
                is_foreign=bool(getattr(event, "foreign", False)),
            )

        # split (forward or reverse)
        old_rate = event.old_rate
        new_rate = event.new_rate
        return FetchedCorpAction(
            symbol=symbol,
            event_type="split",
            ex_date=ex_date_val,
            process_date=process_date_val,
            declared_date=declared_date_val,
            record_date=record_date_val,
            payable_date=payable_date_val,
            split_ratio_from=Decimal(str(old_rate)),
            split_ratio_to=Decimal(str(new_rate)),
            cusip=cusip,
            new_cusip=new_cusip,
            external_id=external_id,
        )
    except (AttributeError, ValueError, TypeError):
        return None
