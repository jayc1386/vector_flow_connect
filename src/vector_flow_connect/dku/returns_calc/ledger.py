"""Parser for the 追溯调整至2022 ledger sheet.

The ledger is byte-identical in shape across the 04 (留本) and 05
(可投) workbooks: row 3 headers, data from row 4, 12 columns
(标记/日期/借方/已实现/账面余额(已实现）/主动配置金额变动/主动配置金额/
非主动配置金额/总资产市值/总份额/净值/内容). Dated rows form DKU's own
unitization: 总份额 changes (mints/burns) on external-capital rows,
净值 = 总资产市值 / 总份额.

Classification predicates are ported from dkup
`scripts/backfill_action_log.py` (`_is_income_or_internal`,
`parse_infusions`, `parse_cash_income`). They are informational —
downstream verification keys off `units_delta`, which is pure data.
"""

from __future__ import annotations

import re
from datetime import datetime

from .canonical import RowKind, Scope

LEDGER_SHEET_NAME = "追溯调整至2022"

_YYYYMM_RE = re.compile(r"20\d{4}")
_INTERNAL_KW = ("赎回", "购买", "追投", "转出至")


def _classify(
    *,
    scope: Scope,
    mark: str,
    debit: float | None,
    realized: str | None,
    content: str,
    is_opening: bool,
) -> RowKind:
    if is_opening or "期初余额" in content:
        return "opening"
    # 捐赠收入 contains 收入 but IS external capital — check first
    # (dkup `_is_income_or_internal` ordering).
    if "捐赠" in content:
        return "external_capital"
    if mark == "分红":
        return "dividend"
    if mark == "浮动收益" or "浮动" in content:
        return "unrealized_funds_mtm"
    if _YYYYMM_RE.fullmatch(mark) or "结构性存款" in content or "利息" in content:
        return "interest_realized" if realized == "Y" else "unrealized_deposit_interest"
    if "授权" in content:
        # 可投资产授权 = board authorization: external capital in the 可投
        # scope; from the 留本 ledger's perspective it is non-corpus.
        return "external_capital" if scope == "ketou" else "internal_move"
    if any(k in content for k in _INTERNAL_KW):
        return "internal_move"
    if realized == "Y" and ("收入" in content or "收益" in content):
        return "income_lumped"
    if isinstance(debit, (int, float)) and debit:
        return "external_capital"
    return "other"


def parse_ledger(ws, *, scope: Scope) -> tuple[list[dict], list[dict]]:
    """Parse the ledger worksheet into (rows, issues).

    Skips undated rows (titles, dividers, blank tails) without
    terminating — the sheet grows and carries cosmetic rows
    (master_record v0.11.2 date-divider lesson). Error-string cells in
    numeric columns null the value and emit an issue instead of
    crashing.
    """
    header = next(ws.iter_rows(min_row=3, max_row=3, max_col=2, values_only=True), None)
    if header is None or header[0] != "标记" or header[1] != "日期":
        raise ValueError(
            f"sheet {ws.title!r} does not look like a 追溯 ledger "
            f"(row-3 header is {header!r}, expected ('标记', '日期'))"
        )

    rows: list[dict] = []
    issues: list[dict] = []
    prev_units: float | None = None
    seen_dated = False

    for idx, r in enumerate(ws.iter_rows(min_row=4, max_col=12, values_only=True), start=4):
        when = r[1]
        if not isinstance(when, datetime):
            continue  # undated divider / blank tail — never break

        def _num(value: object, column: str, row_idx: int = idx) -> float | None:
            if value is None or isinstance(value, (int, float)):
                return float(value) if value is not None else None
            issues.append(
                {
                    "row": row_idx,
                    "column": column,
                    "value": str(value)[:80],
                    "issue": "non_numeric_cell",
                }
            )
            return None

        mark = str(r[0] or "")
        debit = _num(r[2], "借方")
        realized = str(r[3]).strip() if r[3] is not None else None
        units = _num(r[9], "总份额")

        units_delta: float | None
        if not seen_dated:
            units_delta = None  # opening row — its J is the seed, not a delta
        elif units is None or prev_units is None:
            units_delta = None
        else:
            units_delta = units - prev_units

        row_kind = _classify(
            scope=scope,
            mark=mark,
            debit=debit,
            realized=realized,
            content=str(r[11] or ""),
            is_opening=not seen_dated,
        )

        rows.append(
            {
                "scope": scope,
                "as_of": when.date(),
                "row_kind": row_kind,
                "mark": mark,
                "debit": debit,
                "realized_flag": realized,
                "realized_book_balance": _num(r[4], "账面余额(已实现）"),
                "active_alloc_delta": _num(r[5], "主动配置金额变动"),
                "active_alloc": _num(r[6], "主动配置金额"),
                "cash_balance": _num(r[7], "非主动配置金额"),
                "total_mv": _num(r[8], "总资产市值"),
                "total_units": units,
                "unit_nav": _num(r[10], "净值"),
                "units_delta": units_delta,
                "content": str(r[11] or ""),
                "source_locator": f"{LEDGER_SHEET_NAME}!A{idx}",
            }
        )
        seen_dated = True
        if units is not None:
            prev_units = units

    return rows, issues
