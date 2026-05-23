"""Pro-rata split of fund-level PDF events across open lots.

PDFs disclose events at the fund level (a perf-fee deduction or distribution
on a date); `reconcile.reconcile()` groups events by `lot_id`. To make a
fund-level event reduce per-lot mismatches we split it across all lots that
were open at the event_date, proportionally by current units. Each split
event carries the same dedup_key (so `event_id` is stable across
re-extractions) but distinct `lot_id`.

When N ≥ 2, confidence is downgraded to `fuzzy` — the identity of the
affected lot is inferred. Singleton-lot case inherits the original
confidence (no ambiguity).
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from .canonical import event_id


def _open_lots_at(
    positions_df: pd.DataFrame,
    *,
    fund_id: str,
    event_date: date,
) -> pd.DataFrame:
    """Latest position per lot for `fund_id` where `as_of <= event_date` and `units > 0`."""
    fund_positions = positions_df[positions_df["fund_id"] == fund_id]
    if fund_positions.empty:
        return fund_positions
    eligible = fund_positions[fund_positions["as_of"] <= event_date]
    if eligible.empty:
        return eligible
    latest_per_lot = eligible.sort_values("as_of").groupby("lot_id", as_index=False).tail(1)
    open_lots = latest_per_lot[latest_per_lot["units"] > 0]
    return open_lots.reset_index(drop=True)


def _allocate(total: float, weights: list[float], *, decimals: int) -> list[float]:
    """Pro-rata distribute `total` over `weights`, with the rounding residual
    going to the lot with the largest weight (deterministic — keeps event_id
    stable across re-extracts)."""
    if total is None or not weights:
        return []
    total_weight = sum(weights)
    if total_weight == 0:
        share = total / len(weights)
        return [round(share, decimals)] * len(weights)
    raw = [total * (w / total_weight) for w in weights]
    rounded = [round(r, decimals) for r in raw]
    residual = round(total - sum(rounded), decimals)
    if abs(residual) > 0:
        max_idx = max(range(len(weights)), key=lambda i: weights[i])
        rounded[max_idx] = round(rounded[max_idx] + residual, decimals)
    return rounded


def pro_rata_split(
    raw_event: dict[str, Any],
    positions_df: pd.DataFrame,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Split a fund-level raw event across the lots open at `event_date`.

    Returns `(split_events, issue)`. `issue` is non-None when the split
    cannot be performed (no open lots, no positions before event_date, ...).
    """
    fund_id = raw_event["fund_id"]
    event_date = raw_event["event_date"]
    event_type = raw_event["event_type"]
    units_delta = raw_event.get("units_delta")
    cash_delta = raw_event.get("cash_delta")
    source_artifact_hash = raw_event["source_artifact_hash"]
    confidence_self = raw_event.get("confidence", "fuzzy")
    notes_raw = raw_event.get("notes_raw", "") or ""

    lots = _open_lots_at(positions_df, fund_id=fund_id, event_date=event_date)
    if lots.empty:
        return [], {
            "reason": "no_open_lots_at_event_date",
            "fund_id": fund_id,
            "event_date": event_date.isoformat() if event_date else None,
            "event_type": event_type,
            "notes_raw": notes_raw,
        }

    weights = lots["units"].tolist()
    n = len(weights)
    units_splits = (
        _allocate(units_delta, weights, decimals=4) if units_delta is not None else [None] * n
    )
    cash_splits = (
        _allocate(cash_delta, weights, decimals=2) if cash_delta is not None else [None] * n
    )

    confidence = "fuzzy" if n > 1 else confidence_self
    rounded_total_for_dedup = (
        round(float(units_delta), 4)
        if units_delta is not None
        else (round(float(cash_delta), 2) if cash_delta is not None else 0)
    )

    split_events: list[dict[str, Any]] = []
    for i, lot_row in lots.reset_index(drop=True).iterrows():
        lot_id_val = lot_row["lot_id"]
        ev = dict(raw_event)
        ev["lot_id"] = lot_id_val
        ev["units_delta"] = units_splits[i]
        ev["cash_delta"] = cash_splits[i]
        ev["confidence"] = confidence
        ev["notes_raw"] = f"[pro_rata {i + 1}/{n}] {notes_raw}" if n > 1 else notes_raw
        ev["event_id"] = event_id(
            source_artifact_hash,
            event_type,
            f"{lot_id_val}:{event_date.isoformat()}:{event_type}:{rounded_total_for_dedup}",
        )
        split_events.append(ev)
    return split_events, None


def attribute_raw_events(
    raw_events: list[dict[str, Any]],
    positions_df: pd.DataFrame,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Run pro_rata_split over a batch; accumulate split events + issues."""
    all_split: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for ev in raw_events:
        split, issue = pro_rata_split(ev, positions_df)
        all_split.extend(split)
        if issue is not None:
            issues.append(issue)
    return all_split, issues
