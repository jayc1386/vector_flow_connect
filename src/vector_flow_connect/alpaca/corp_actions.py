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
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from vector_flow_connect.alpaca.settings import AlpacaCredentials

# Alpaca → prism event_type mapping. Mirrors the verified live API:
# response payloads carry plural keys (`cash_dividends`,
# `forward_splits`, `reverse_splits`).
_DIVIDEND_KEYS = {"cash_dividends", "cash_dividend"}
_FORWARD_SPLIT_KEYS = {"forward_splits", "forward_split"}
_REVERSE_SPLIT_KEYS = {"reverse_splits", "reverse_split"}


class FetchedCorpAction(BaseModel):
    """One corp-action event as returned by a `CorpActionsFetcher`.
    Vendor-agnostic shape; concrete fetchers normalize Alpaca's
    type-specific event models into this single shape."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    event_type: Literal["dividend", "split"]
    ex_date: date
    process_date: date
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
    import happens inside the constructor so unit tests using fakes
    don't pay the import cost or require live env vars.
    """

    def __init__(self, *, api_key: str, api_secret: str) -> None:
        from alpaca.data.historical.corporate_actions import CorporateActionsClient

        self._client = CorporateActionsClient(api_key, api_secret)

    @classmethod
    def from_credentials(cls, credentials: AlpacaCredentials) -> AlpacaCorpActionsFetcher:
        return cls(
            api_key=credentials.api_key,
            api_secret=credentials.secret_key,
        )

    def get_corp_actions(
        self,
        *,
        symbols: list[str],
        start: date,
        end: date,
    ) -> list[FetchedCorpAction]:
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
        )
        result = self._client.get_corporate_actions(req)
        out: list[FetchedCorpAction] = []
        for ca_type_key, events in result.data.items():  # pyright: ignore[reportAttributeAccessIssue]
            event_type = _classify_alpaca_type(ca_type_key)
            if event_type is None:
                continue
            for e in events:
                fetched = _normalize_alpaca_event(e, event_type)
                if fetched is not None:
                    out.append(fetched)
        return out


def _classify_alpaca_type(alpaca_type_key: str) -> Literal["dividend", "split"] | None:
    if alpaca_type_key in _DIVIDEND_KEYS:
        return "dividend"
    if alpaca_type_key in _FORWARD_SPLIT_KEYS or alpaca_type_key in _REVERSE_SPLIT_KEYS:
        return "split"
    return None


def _normalize_alpaca_event(
    event: Any, event_type: Literal["dividend", "split"]
) -> FetchedCorpAction | None:
    """Convert an alpaca-py event object into our vendor-agnostic shape.
    Returns None if the event is malformed in a way we can't recover from.

    `event` is typed as Any because alpaca-py's per-type event models
    (`CashDividend`, `ForwardSplit`, `ReverseSplit`, etc.) share field
    names but no common base class with full type info; we duck-type
    against the documented field surface.
    """
    try:
        symbol = str(event.symbol)
        external_id = str(getattr(event, "id", "")) or None
        ex_date_val = event.ex_date
        process_date_val = event.process_date
        record_date_val = getattr(event, "record_date", None)
        payable_date_val = getattr(event, "payable_date", None)

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
