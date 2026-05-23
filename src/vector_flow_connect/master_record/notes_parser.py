"""Regex extractors for free-text event notes.

Inputs are cell strings from the `注释`/`备注`/`总结` columns of snapshot
sheets. Outputs are canonical event dicts; the caller fills provenance
fields (`source_artifact`, `source_locator`, `recorded_at`, etc.) and
the lot context.

Five distinct text patterns recognized in v1:

1. Full redemption (`全部赎回`) — one event per match.
2. Cash dividend (`每单位分红 ... 合计收到的现金红利X元`) — one event per
   simple annotation. Multi-period annotations
   (`2021和2022年每单位分红A元和B元`) emit a single event with the
   aggregated cash amount, flagged `confidence='fuzzy'`.
3. DRIP / dividend reinvestment (`红利再投资X份`) — one event per match;
   `payout_form='reinvested'`, shares credited rather than cash.
4. Performance fee (`已累计扣除X份额作为业绩报酬`).

Partial-redemption pattern is deferred (see DEFERRED.md). Numbers
tolerate Western thousand-separators (e.g. `493,490.59`).
"""

from __future__ import annotations

import re
from calendar import monthrange
from datetime import date

from .canonical import (
    EXTRACTOR_NAME,
    EXTRACTOR_VERSION,
    SCHEMA_VERSION,
    SOURCE_ID,
    empty_event,
    event_id,
)

# A number with optional Western thousand-separator commas and decimals.
# The first alternative *requires* at least one comma-group so it can't
# match a prefix of an uncommaed number (e.g. parsing "282811.4" → "282").
_NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"


def _num(s: str) -> float:
    """Strip commas + cast to float. Used after a `_NUM` capture."""
    return float(s.replace(",", ""))


REDEMPTION_FULL = re.compile(
    r"于(?P<y>\d{4})年(?P<m>\d{1,2})月(?P<d>\d{1,2})日"
    r"全部赎回"
    r"[，,。]?\s*"
    r"(?:赎回净值|赎回日净额|赎回日净值)"
    rf"\s*(?P<nav>{_NUM})"
)

# Simple, single-period cash dividend.
DIVIDEND_SIMPLE = re.compile(
    r"(?P<y>\d{4})年(?P<m>\d{1,2})月\s*"
    r"每单位分红\s*(?P<per_unit>" + _NUM + r")\s*元"
    r"[，,]\s*分红(?:总)?份额(?:为)?\s*(?P<eligible>" + _NUM + r")\s*份?"
    r"[，,]\s*合计收到的现金红利\s*(?P<cash>" + _NUM + r")\s*元"
)

# Multi-period: `... 2021和2022年每单位分红 0.2470元和0.0780元 ...`.
# Conservatively captures only the aggregate cash total + total eligible
# units; per-period decomposition is left as fuzzy and recorded in
# notes_raw for human review.
DIVIDEND_MULTI = re.compile(
    r"(?P<y1>\d{4})(?:和\d{4})+年\s*"
    r"每单位分红\s*(?P<per_units>(?:" + _NUM + r"元和?\s*)+)"
    r"[，,]\s*分红(?:总)?份额(?:为)?\s*(?P<eligible>" + _NUM + r")\s*份?"
    r"[，,]\s*合计收到的现金红利\s*(?P<cash>" + _NUM + r")\s*元"
)

# DRIP (red-li zai-tou-zi).
DRIP = re.compile(
    r"(?P<y>\d{4})年(?P<m>\d{1,2})月(?P<d>\d{1,2})日[，,]\s*"
    r"红利再投资\s*(?P<units>" + _NUM + r")\s*份"
    r"[，,]?\s*单位净值\s*(?P<nav>" + _NUM + r")\s*元"
)

PERF_FEE = re.compile(r"已累计扣除\s*(?P<units>" + _NUM + r")\s*份额作为业绩报酬")

# Fund-level *cumulative* DRIP observation — records the running total
# of DRIP shares credited to a fund as of the snapshot date. Not an
# event (it's a balance), so handled separately by `parse_observations`.
CUMULATIVE_DRIP = re.compile(r"累计红利再投资份额\s*(?P<units>" + _NUM + r")")


def _month_end(year: int, month: int) -> date:
    return date(year, month, monthrange(year, month)[1])


def parse(
    text: str | None,
    *,
    lot_context: dict,
    source_locator: str,
    source_artifact: str,
    source_artifact_hash: str,
    recorded_at: date,
    extracted_at,
) -> list[dict]:
    """Scan a cell for any of the v1 patterns.

    `lot_context` carries the lot this notes cell is attached to:
        {
            "lot_id": str,
            "fund_id": str,
            "source_fund_string": str,
            "units_at_lot": float,      # initial units of the lot, for full-redemption
        }

    Returns a list of canonical event dicts.
    """
    out: list[dict] = []
    if not text or not isinstance(text, str):
        return out

    common = {
        "fund_id": lot_context["fund_id"],
        "source_fund_string": lot_context["source_fund_string"],
        "lot_id": lot_context["lot_id"],
        "currency": "CNY",
        "confidence": "clean",
        "data_quality_flag": "clean",
        "source_artifact": source_artifact,
        "source_artifact_hash": source_artifact_hash,
        "source_locator": source_locator,
        "source_id": SOURCE_ID,
        "extractor_name": EXTRACTOR_NAME,
        "extractor_version": EXTRACTOR_VERSION,
        "schema_version": SCHEMA_VERSION,
        "extracted_at": extracted_at,
        "recorded_at": recorded_at,
    }

    lot_id_val = lot_context["lot_id"]

    # --- Full redemption ---
    for m in REDEMPTION_FULL.finditer(text):
        evt = empty_event()
        evt.update(common)
        d = date(int(m["y"]), int(m["m"]), int(m["d"]))
        nav = _num(m["nav"])
        units = lot_context.get("units_at_lot")
        evt.update(
            event_type="redemption",
            event_date=d,
            valid_from=d,
            per_unit_amount=nav,
            units_delta=(-units) if units is not None else None,
            cash_delta=(units * nav) if units is not None else None,
            notes_raw=m.group(0),
            event_id=event_id(
                source_artifact_hash,
                "redemption",
                f"{lot_id_val}:{d.isoformat()}",
            ),
        )
        out.append(evt)

    # --- Cash dividend, multi-period (try BEFORE simple — multi-period
    # text also matches the simple pattern by chance otherwise) ---
    multi_spans: list[tuple[int, int]] = []
    for m in DIVIDEND_MULTI.finditer(text):
        multi_spans.append(m.span())
        evt = empty_event()
        evt.update(common)
        # Best-effort event_date: month-end of December of the *last*
        # year mentioned. Year capture must anchor at 年 or 和 — naive
        # `\d{4}` finds 4-digit substrings inside decimal numbers
        # (e.g. "0.0780" → "0780").
        years_in = re.findall(r"\d{4}(?=年|和)", m.group(0))
        last_year = int(years_in[-1]) if years_in else None
        d = _month_end(last_year, 12) if last_year else recorded_at
        eligible = _num(m["eligible"])
        cash = _num(m["cash"])
        evt.update(
            event_type="dividend",
            payout_form="cash",
            event_date=d,
            valid_from=d,
            eligible_units=eligible,
            cash_delta=cash,
            confidence="fuzzy",  # per-period decomposition not extracted
            notes_raw=m.group(0),
            event_id=event_id(
                source_artifact_hash,
                "dividend",
                f"{lot_id_val}:{d.isoformat()}:cash:{round(cash, 2)}",
            ),
        )
        out.append(evt)

    # --- Cash dividend, simple single-period ---
    for m in DIVIDEND_SIMPLE.finditer(text):
        # Skip any simple match whose span overlaps a multi-period match
        # we already emitted, to avoid double-counting.
        if any(s <= m.start() < e for s, e in multi_spans):
            continue
        evt = empty_event()
        evt.update(common)
        d = _month_end(int(m["y"]), int(m["m"]))
        per_unit = _num(m["per_unit"])
        eligible = _num(m["eligible"])
        cash = _num(m["cash"])
        # Invariant: per_unit * eligible ≈ cash (1 RMB tol).
        confidence = "clean" if abs(per_unit * eligible - cash) <= 1.0 else "reconcile_fail"
        evt.update(
            event_type="dividend",
            payout_form="cash",
            event_date=d,
            valid_from=d,
            per_unit_amount=per_unit,
            eligible_units=eligible,
            cash_delta=cash,
            confidence=confidence,
            notes_raw=m.group(0),
            event_id=event_id(
                source_artifact_hash,
                "dividend",
                f"{lot_id_val}:{d.isoformat()}:cash:{round(cash, 2)}",
            ),
        )
        out.append(evt)

    # --- DRIP (dividend reinvestment, shares-credited) ---
    for m in DRIP.finditer(text):
        evt = empty_event()
        evt.update(common)
        d = date(int(m["y"]), int(m["m"]), int(m["d"]))
        units = _num(m["units"])
        nav = _num(m["nav"])
        evt.update(
            event_type="dividend",
            payout_form="reinvested",
            event_date=d,
            valid_from=d,
            units_delta=units,
            per_unit_amount=nav,
            notes_raw=m.group(0),
            event_id=event_id(
                source_artifact_hash,
                "dividend",
                f"{lot_id_val}:{d.isoformat()}:drip:{round(units, 4)}",
            ),
        )
        out.append(evt)

    # --- Performance fee ---
    for m in PERF_FEE.finditer(text):
        evt = empty_event()
        evt.update(common)
        units = _num(m["units"])
        evt.update(
            event_type="perf_fee",
            event_date=recorded_at,  # placeholder; pattern doesn't carry date
            valid_from=recorded_at,
            units_delta=-units,
            confidence="fuzzy",
            notes_raw=m.group(0),
            event_id=event_id(
                source_artifact_hash,
                "perf_fee",
                f"{lot_id_val}:units:{round(units, 4)}",
            ),
        )
        out.append(evt)

    return out


def parse_observations(
    text: str | None,
    *,
    fund_id: str,
    source_fund_string: str,
    source_locator: str,
    recorded_at: date,
    source_artifact: str | None = None,
    source_artifact_hash: str | None = None,
    extracted_at=None,
) -> list[dict]:
    """Extract fund-level *observations* (balance-style, not event-style).

    Currently picks up only `累计红利再投资份额X` — the cumulative DRIP
    balance. Returns a list of observation dicts; empty if no pattern
    matches.
    """
    out: list[dict] = []
    if not text or not isinstance(text, str):
        return out
    for m in CUMULATIVE_DRIP.finditer(text):
        out.append(
            {
                "observation_type": "cumulative_drip_units",
                "fund_id": fund_id,
                "fund_code": None,
                "source_fund_string": source_fund_string,
                "as_of": recorded_at,
                "value": _num(m["units"]),
                "source_locator": source_locator,
                "source_artifact": source_artifact,
                "source_artifact_hash": source_artifact_hash,
                "source_id": SOURCE_ID,
                "extractor_name": EXTRACTOR_NAME,
                "extractor_version": EXTRACTOR_VERSION,
                "schema_version": SCHEMA_VERSION,
                "extracted_at": extracted_at,
                "notes_raw": m.group(0),
            }
        )
    return out


def find_patterns(text: str) -> dict[str, list[re.Match]]:
    """Debug helper — returns matches by pattern name without building events."""
    if not text or not isinstance(text, str):
        return {
            k: [] for k in ("redemption", "dividend_simple", "dividend_multi", "drip", "perf_fee")
        }
    return {
        "redemption": list(REDEMPTION_FULL.finditer(text)),
        "dividend_simple": list(DIVIDEND_SIMPLE.finditer(text)),
        "dividend_multi": list(DIVIDEND_MULTI.finditer(text)),
        "drip": list(DRIP.finditer(text)),
        "perf_fee": list(PERF_FEE.finditer(text)),
    }
