"""Completeness tracking — every fund × every expected month, with cross-source NAV check."""

from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, timezone
from typing import Any

import pandas as pd

_NAV_MATCH_TOLERANCE = 5e-4  # 5 bps; absorbs 4-decimal rounding noise without masking real diffs
_DATE_NEIGHBOURHOOD_DAYS = 5  # master_record snapshot may be a few days off month-end


def _month_end_range(start: date, end: date) -> list[date]:
    """Every month-end date from `start` (inclusive) through `end` (inclusive)."""
    out: list[date] = []
    y, m = start.year, start.month
    while True:
        d = date(y, m, monthrange(y, m)[1])
        if d > end:
            break
        out.append(d)
        m += 1
        if m > 12:
            m = 1
            y += 1
    return out


def _nav_master_record_at(
    positions_df: pd.DataFrame,
    *,
    fund_id: str,
    target_date: date,
) -> float | None:
    """Find the master_record NAV for `fund_id` at a snapshot near `target_date`.

    Uses a ±N-day neighbourhood because master_record snapshots aren't always
    on the calendar month-end. Returns None if no snapshot lands in range.
    """
    rows = positions_df[positions_df["fund_id"] == fund_id]
    if rows.empty:
        return None
    rows = rows.assign(date_diff=rows["as_of"].apply(lambda d: abs((d - target_date).days)))
    nearby = rows[rows["date_diff"] <= _DATE_NEIGHBOURHOOD_DAYS]
    if nearby.empty:
        return None
    nav = nearby.sort_values("date_diff").iloc[0]["nav"]
    return float(nav) if nav is not None else None


def build_completeness(
    funds_df: pd.DataFrame,
    positions_df: pd.DataFrame,
    observations_df: pd.DataFrame,
    pdf_manifest_artifacts: list[dict[str, Any]],
    *,
    today: date | None = None,
) -> pd.DataFrame:
    """Build the (fund × expected_month) completeness table."""
    today = today or date.today()
    pdf_by_key: dict[tuple[str, date], dict[str, Any]] = {}
    for art in pdf_manifest_artifacts:
        period = art.get("period_end")
        if isinstance(period, str):
            period = date.fromisoformat(period)
        pdf_by_key[(art["fund_id_stub"], period)] = art

    # PDF reporting dates aren't always strict calendar month-end — trust
    # wrappers (e.g. 外贸信托-信衡1号) report on the last business day. Bucket
    # each NAV by the month-end of its containing month so the join below
    # picks them up by `expected_month`. Multiple PDFs per bucket (e.g. monthly
    # + quarterly for the same period, or share-class variants) accumulate
    # into a list of (value, share_class) — match succeeds if ANY of them
    # aligns with master_record; the report column shows which share-class
    # variant won.
    nav_pdf_lookup: dict[tuple[str, date], list[tuple[float, str | None]]] = {}
    nav_rows = observations_df[observations_df["observation_type"] == "nav_per_unit"]
    for _, r in nav_rows.iterrows():
        d = r["as_of"]
        if isinstance(d, str):
            d = date.fromisoformat(d)
        bucket = date(d.year, d.month, monthrange(d.year, d.month)[1])
        share_class = None
        notes = r.get("notes_raw") or ""
        if "share_class=" in notes:
            for part in notes.split(";"):
                if part.startswith("share_class="):
                    share_class = part[len("share_class=") :] or None
                    break
        nav_pdf_lookup.setdefault((r["fund_id"], bucket), []).append(
            (float(r["value"]), share_class)
        )

    rows: list[dict[str, Any]] = []
    for _, fund in funds_df.iterrows():
        first_seen = fund["first_seen_as_of"]
        if first_seen is None or (isinstance(first_seen, float) and pd.isna(first_seen)):
            continue
        if isinstance(first_seen, str):
            first_seen = date.fromisoformat(first_seen)
        for month_end in _month_end_range(first_seen, today):
            received_art = pdf_by_key.get((fund["fund_id"], month_end))
            nav_pdf_list = nav_pdf_lookup.get((fund["fund_id"], month_end), [])
            nav_mr = _nav_master_record_at(
                positions_df, fund_id=fund["fund_id"], target_date=month_end
            )
            nav_match: bool | None
            nav_pdf_chosen: float | None = None
            nav_pdf_share_class: str | None = None
            if not nav_pdf_list or nav_mr is None:
                nav_match = None
                if nav_pdf_list:
                    nav_pdf_chosen, nav_pdf_share_class = nav_pdf_list[0]
            else:
                # Match against master_record if ANY of the PDF NAVs aligns.
                # Pick the closest as the canonical comparison value; surface
                # its share_class so the operator can chase divergence with
                # the client.
                chosen = min(nav_pdf_list, key=lambda vsc: abs(vsc[0] - nav_mr))
                nav_pdf_chosen, nav_pdf_share_class = chosen
                nav_match = abs(nav_pdf_chosen - nav_mr) <= _NAV_MATCH_TOLERANCE
            rows.append(
                {
                    "fund_id": fund["fund_id"],
                    "fund_name_zh": fund.get("name_zh"),
                    "expected_month": month_end,
                    "received_artifact": received_art["filename"] if received_art else None,
                    "received_artifact_hash": received_art["artifact_hash"]
                    if received_art
                    else None,
                    "extracted": received_art is not None,
                    "reconciled": "clean" if received_art else "not_run",
                    "nav_pdf": nav_pdf_chosen,
                    "nav_pdf_share_class": nav_pdf_share_class,
                    "nav_master_record": nav_mr,
                    "nav_match": nav_match,
                    "as_of_generated": datetime.now(timezone.utc),
                }
            )
    return pd.DataFrame(rows)
