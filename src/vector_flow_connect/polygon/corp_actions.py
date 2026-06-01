"""Polygon (Massive) corporate-actions fetcher.

Second source (after Alpaca) for dividend + split events, motivated by
Polygon exposing `declaration_date` as a first-class, SEC-filing-derived
field — the value Alpaca's deprecated-announcements sidecar gets wrong
(systematically late; see prism's `reference_alpaca_announcements_data_quality`).

Endpoints (legacy `/v3/reference/*`, where `declaration_date` is both
returned AND filterable; the newer `/stocks/v1` path can't filter on it):
- `/v3/reference/dividends` — `declaration_date`, `ex_dividend_date`,
  `record_date`, `pay_date`, `cash_amount`, `dividend_type`, `id`.
- `/v3/reference/splits` — `execution_date`, `split_from`, `split_to`,
  `id`. (Splits carry no declaration_date.)

Rate-limit strategy: query **whole-dataset by date range** (ticker is
optional on both endpoints) and filter to the requested symbols in
Python. For a ~500-name universe this is dozens of paginated requests,
not 500 per-ticker calls — critical under the free tier's 5 req/min cap.

Normalizes into the vendor-agnostic `FetchedCorpAction` shared shape.
Polygon has no CUSIP on these endpoints (→ None) and no `process_date`
(→ defaulted to ex/execution date, mirroring the Alpaca announcements
normaliser). Currency (usually USD) is not carried on `FetchedCorpAction`
— the consumer sets it, same as the Alpaca path.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

# Vendor-agnostic shared shape. Lives under alpaca/ in the v0 flat layout;
# reused here. (Promote `FetchedCorpAction` + the Fetcher Protocols to the
# package root when convenient — see the note in `alpaca/_base.py`.)
from vector_flow_connect.alpaca.corp_actions import FetchedCorpAction
from vector_flow_connect.polygon._client import PolygonRestClient
from vector_flow_connect.polygon.settings import PolygonCredentials

_DIVIDENDS_PATH = "/v3/reference/dividends"
_SPLITS_PATH = "/v3/reference/splits"
_PAGE_LIMIT = 1000


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


class PolygonCorpActionsFetcher:
    """Concrete (structural) `CorpActionsFetcher` backed by Polygon's
    REST reference endpoints.

    Construct via `from_credentials()` for production, or inject a fake
    `client` (any object exposing `paginate(path, params)`) in tests.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        client: Any | None = None,
        min_request_interval_secs: float = 12.0,
    ) -> None:
        if client is None:
            if not api_key:
                raise ValueError("api_key is required when no client is injected")
            client = PolygonRestClient(
                api_key=api_key, min_request_interval_secs=min_request_interval_secs
            )
        self._client = client

    @classmethod
    def from_credentials(cls, credentials: PolygonCredentials) -> PolygonCorpActionsFetcher:
        return cls(api_key=credentials.api_key)

    def get_corp_actions(
        self,
        *,
        symbols: list[str],
        start: date,
        end: date,
    ) -> list[FetchedCorpAction]:
        if not symbols:
            return []
        symset = {s.upper() for s in symbols}
        out: list[FetchedCorpAction] = []

        for row in self._client.paginate(
            _DIVIDENDS_PATH,
            {
                "ex_dividend_date.gte": start.isoformat(),
                "ex_dividend_date.lte": end.isoformat(),
                "limit": _PAGE_LIMIT,
            },
        ):
            fetched = _normalize_dividend(row, symset)
            if fetched is not None:
                out.append(fetched)

        for row in self._client.paginate(
            _SPLITS_PATH,
            {
                "execution_date.gte": start.isoformat(),
                "execution_date.lte": end.isoformat(),
                "limit": _PAGE_LIMIT,
            },
        ):
            fetched = _normalize_split(row, symset)
            if fetched is not None:
                out.append(fetched)

        return out


def _normalize_dividend(row: dict[str, Any], symset: set[str]) -> FetchedCorpAction | None:
    ticker = str(row.get("ticker") or "").upper()
    if not ticker or ticker not in symset:
        return None
    ex = _parse_date(row.get("ex_dividend_date"))
    cash = row.get("cash_amount")
    if ex is None or cash is None:
        return None
    # `dividend_type` (v3) / `distribution_type` (v1): SC = special cash.
    dtype = str(row.get("dividend_type") or row.get("distribution_type") or "").upper()
    external_id = row.get("id")
    return FetchedCorpAction(
        symbol=ticker,
        event_type="dividend",
        ex_date=ex,
        process_date=ex,  # Polygon has no process_date; default to ex_date
        declared_date=_parse_date(row.get("declaration_date")),
        record_date=_parse_date(row.get("record_date")),
        payable_date=_parse_date(row.get("pay_date")),
        cash_amount=Decimal(str(cash)),
        external_id=str(external_id) if external_id is not None else None,
        is_special=(dtype == "SC"),
    )


def _normalize_split(row: dict[str, Any], symset: set[str]) -> FetchedCorpAction | None:
    ticker = str(row.get("ticker") or "").upper()
    if not ticker or ticker not in symset:
        return None
    exec_date = _parse_date(row.get("execution_date"))
    split_from = row.get("split_from")
    split_to = row.get("split_to")
    if exec_date is None or split_from is None or split_to is None:
        return None
    external_id = row.get("id")
    return FetchedCorpAction(
        symbol=ticker,
        event_type="split",
        ex_date=exec_date,
        process_date=exec_date,
        split_ratio_from=Decimal(str(split_from)),
        split_ratio_to=Decimal(str(split_to)),
        external_id=str(external_id) if external_id is not None else None,
    )
