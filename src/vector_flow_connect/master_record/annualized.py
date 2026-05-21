"""Reader for the 收益年化 (annualized returns) sheet.

Layout (per 20260430 workbook inspection):
- Column A: dates
- Column B: signed cashflows (− subscriptions, + redemptions/dividends)
- Row 1: opening balance — A=as-of, B=−(opening NAV). Synthetic, not a
  real event.
- Last positive row: terminal value — closing NAV. Also synthetic.
- One trailing row with the XIRR result in column B (no date in A).

In v1 this is a **reconciliation oracle**, not a primary event source.
The caller diffs the extracted events' cashflow series against this
sheet's `cashflows` list.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from openpyxl.worksheet.worksheet import Worksheet


def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_sheet(ws: Worksheet) -> dict:
    """Parse the 收益年化 sheet into a structured dict.

    Returns:
        {
            "opening_balance": (date, amount) | None,
            "cashflows": [(date, amount) ...],  # real events only
            "terminal_value": (date, amount) | None,
            "xirr_result": float | None,
            "raw_rows": [(date, amount) ...],   # everything, for audit
        }
    """
    raw: list[tuple[date | None, float | None]] = []
    xirr_result: float | None = None

    for row in ws.iter_rows(min_row=1, max_row=ws.max_row, values_only=True):
        a = row[0] if len(row) > 0 else None
        b = row[1] if len(row) > 1 else None
        d = _as_date(a)
        amt = _as_float(b)
        if d is None and amt is not None and abs(amt) < 10:
            # A dateless small-number row is the XIRR result.
            xirr_result = amt
            continue
        if d is None and amt is None:
            continue
        raw.append((d, amt))

    if not raw:
        return {
            "opening_balance": None,
            "cashflows": [],
            "terminal_value": None,
            "xirr_result": xirr_result,
            "raw_rows": [],
        }

    # Opening balance = first row (typically a negative ≈ portfolio NAV
    # at start, no real cashflow).
    opening = raw[0]

    # Terminal value: the largest positive amount in the series is
    # *typically* the closing valuation. More robust: the LAST row of
    # the series, since XIRR rows are written chronologically and the
    # closing terminal value is the final entry. Inspect-confirmed in
    # 20260430: row 23 has the closing terminal value 31.8M.
    terminal = raw[-1]

    middle = raw[1:-1]
    cashflows = [(d, a) for d, a in middle if d is not None and a is not None]

    return {
        "opening_balance": opening,
        "cashflows": cashflows,
        "terminal_value": terminal,
        "xirr_result": xirr_result,
        "raw_rows": raw,
    }
