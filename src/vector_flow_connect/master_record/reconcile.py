"""Intra-workbook reconciliation checks.

Three checks (v1):
  1. Events ↔ 收益年化 cashflows — every cashflow in the annualized
     sheet should have a matching extracted event within a small
     date tolerance.
  2. Per-lot unit count over time — for each (lot, snapshot), the
     observed units should equal initial_units − redemptions −
     perf-fee deductions (+ DRIP reinvestments) up to that snapshot.
  3. Dividend invariant — already enforced inside notes_parser
     (per_unit × eligible ≈ cash). This module just summarizes
     the flagged rows.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, TypedDict

import pandas as pd

from ._inherited_canonical_contract import resolve_data_quality_flag
from .canonical import SCHEMA_VERSION, SOURCE_ID


# ---------------------------------------------------------------------------
# Return-shape contract — TypedDicts for the three break categories and the
# top-level `reconcile()` return. These are load-bearing for prism's typed-
# tool mapping (`record_cash_flow` / `record_data_quality_finding`); future
# bumps that change a field name or type must update these in the same diff
# so the contract is diff-visible.
# ---------------------------------------------------------------------------

CashflowSide = Literal["annualized_only", "events_only"]


class CashflowIssue(TypedDict):
    """A cashflow seen in only one of (annualized sheet, events). The
    annualized sheet is a fund-of-funds aggregate view (no per-fund
    breakdown), so there is no `fund_id` on these rows.

    `date` (not `as_of`) is kept as the field name because the cashflow's
    natural anchor is the cashflow date, not a position-snapshot timestamp.
    """

    side: CashflowSide
    date: str  # ISO date
    amount: float


class UnitIssue(TypedDict):
    """A (lot, snapshot) where observed units don't equal
    initial_units − Σ(units_delta up to that snapshot)."""

    lot_id: str
    fund_id: str | None
    as_of: str  # ISO date
    expected: float
    observed: float
    diff: float  # observed − expected


class DripGapRow(TypedDict):
    """A fund where the cumulative DRIP balance annotation exceeds the sum
    of attested per-event DRIP `units_delta` rows. Indicates real DRIPs
    that DKU didn't write into 注释 as dated annotations."""

    fund_id: str
    source_fund_string: str
    as_of: str  # ISO date
    cumulative_balance: float
    attested_per_event_sum: float
    gap: float  # cumulative − attested


class ReconcileResult(TypedDict):
    """Top-level dict returned by `reconcile()`. The list-typed fields are
    the inputs prism's `record_data_quality_finding` typed tool maps over;
    scalar fields are summary counts + the markdown report path."""

    cashflow_issues: list[CashflowIssue]
    unit_issues: list[UnitIssue]
    drip_gap_rows: list[DripGapRow]
    dividend_fails: int
    report_path: str


def reconcile(
    events_df: pd.DataFrame,
    positions_df: pd.DataFrame,
    annualized: dict | None,
    *,
    out_path: str | Path,
    observations_df: pd.DataFrame | None = None,
    date_tolerance_days: int = 3,
    unit_tolerance: float = 1.0,
) -> ReconcileResult:
    """Run all three checks and write a markdown report.

    Returns a dict with summary counts and the report path.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = ["# Master-record reconciliation report\n"]

    # ---------- 1. Cashflow reconciliation ----------
    cashflow_issues: list[dict] = []
    if annualized and annualized.get("cashflows"):
        annualized_cf = pd.DataFrame(annualized["cashflows"], columns=["date", "amount"])
        annualized_cf["date"] = pd.to_datetime(annualized_cf["date"])

        # Scope reconciliation to the annualized sheet's date window.
        # Events outside this window can't reconcile against rows that
        # don't exist, so they aren't flagged as `events_only`.
        opening_date = annualized.get("opening_balance")
        opening_date = pd.to_datetime(opening_date[0]) if opening_date and opening_date[0] else None
        terminal_date = annualized.get("terminal_value")
        terminal_date = (
            pd.to_datetime(terminal_date[0]) if terminal_date and terminal_date[0] else None
        )

        # Build the same series from events, scoped to the window.
        if not events_df.empty:
            evt_cf = events_df.dropna(subset=["cash_delta"]).copy()
            evt_cf["date"] = pd.to_datetime(evt_cf["event_date"])
            if opening_date is not None:
                evt_cf = evt_cf[evt_cf["date"] >= opening_date]
            if terminal_date is not None:
                evt_cf = evt_cf[evt_cf["date"] <= terminal_date]
            evt_cf = evt_cf[["date", "cash_delta"]].rename(columns={"cash_delta": "amount"})
        else:
            evt_cf = pd.DataFrame(columns=["date", "amount"])

        tol = pd.Timedelta(days=date_tolerance_days)

        # For each annualized cashflow, find the closest event cashflow
        # within tolerance with the same sign.
        matched_evt_idx: set[int] = set()
        for _, row in annualized_cf.iterrows():
            d, a = row["date"], row["amount"]
            same_sign = evt_cf[(evt_cf["amount"] * a > 0)]
            within = same_sign[(same_sign["date"] - d).abs() <= tol]
            within = within[(within["amount"] - a).abs() < max(abs(a) * 0.01, 1.0)]
            if within.empty:
                cashflow_issues.append(
                    {
                        "side": "annualized_only",
                        "date": d.date().isoformat(),
                        "amount": float(a),
                    }
                )
            else:
                matched_evt_idx.add(within.index[0])

        # Events not matched to any annualized cashflow (cash-bearing only).
        for idx, row in evt_cf.iterrows():
            if idx in matched_evt_idx:
                continue
            # DRIP dividends won't appear in 收益年化 — skip them silently.
            # We can identify them by joining back to events_df.
            evt_row = events_df.loc[idx] if idx in events_df.index else None
            if evt_row is not None and evt_row.get("payout_form") == "reinvested":
                continue
            cashflow_issues.append(
                {
                    "side": "events_only",
                    "date": row["date"].date().isoformat(),
                    "amount": float(row["amount"]),
                }
            )

        lines.append("## 1. Cashflows: events ↔ 收益年化")
        window_str = (
            f"[{opening_date.date()} → {terminal_date.date()}]"
            if opening_date is not None and terminal_date is not None
            else "open"
        )
        lines.append(f"- Reconciliation window: {window_str}")
        lines.append(f"- Annualized cashflows: {len(annualized_cf)}")
        lines.append(f"- Event cash-bearing rows (in window): {len(evt_cf)}")
        lines.append(f"- Unmatched: {len(cashflow_issues)}")
        if cashflow_issues:
            lines.append("")
            lines.append("| side | date | amount |")
            lines.append("|---|---|---|")
            for issue in cashflow_issues[:50]:
                lines.append(f"| {issue['side']} | {issue['date']} | {issue['amount']:,.2f} |")
            if len(cashflow_issues) > 50:
                lines.append(f"| ... | ... | _{len(cashflow_issues) - 50} more rows_ |")
        lines.append("")
    else:
        lines.append("## 1. Cashflows: events ↔ 收益年化")
        lines.append("- 收益年化 not parsed (sheet missing or empty).")
        lines.append("")

    # ---------- 2. Per-lot unit-count reconciliation ----------
    unit_issues: list[dict] = []
    if not events_df.empty and not positions_df.empty:
        unit_events = events_df.dropna(subset=["units_delta"]).copy()
        unit_events["event_date"] = pd.to_datetime(unit_events["event_date"])
        # build a lot_id → fund_id lookup
        lot_to_fund = (
            events_df.dropna(subset=["lot_id"])
            .drop_duplicates("lot_id")[["lot_id", "fund_id"]]
            .set_index("lot_id")["fund_id"]
            .to_dict()
        )

        for lot_id_val, lot_events in unit_events.groupby("lot_id"):
            if lot_id_val is None:
                continue
            lot_events = lot_events.sort_values("event_date")
            lot_positions = positions_df[positions_df["lot_id"] == lot_id_val].copy()
            lot_positions["as_of"] = pd.to_datetime(lot_positions["as_of"])
            lot_positions = lot_positions.sort_values("as_of")

            for _, pos_row in lot_positions.iterrows():
                as_of = pos_row["as_of"]
                observed = pos_row.get("units")
                if observed is None or pd.isna(observed):
                    continue
                applicable = lot_events[lot_events["event_date"] <= as_of]
                expected = applicable["units_delta"].sum()
                if abs(expected - observed) > unit_tolerance:
                    unit_issues.append(
                        {
                            "lot_id": lot_id_val,
                            "fund_id": lot_to_fund.get(lot_id_val),
                            "as_of": as_of.date().isoformat(),
                            "expected": float(expected),
                            "observed": float(observed),
                            "diff": float(observed - expected),
                        }
                    )

    # Funds known to have a DRIP gap — flag those issues as "explained".
    drip_gap_funds: set[str] = set()
    if observations_df is not None and not observations_df.empty:
        cum = observations_df[observations_df["observation_type"] == "cumulative_drip_units"]
        drip_gap_funds = set(cum["fund_id"].unique())

    # Per-fund summary first; the per-row detail is in the parquet output.
    by_fund: dict[str, list[dict]] = {}
    for issue in unit_issues:
        by_fund.setdefault(issue.get("fund_id") or "(unknown)", []).append(issue)

    lines.append("## 2. Per-lot unit-count reconciliation")
    lines.append(f"- Mismatches: {len(unit_issues)} across {len(by_fund)} funds")
    lines.append("")
    if by_fund:
        lines.append("| fund_id | mismatch_count | likely cause |")
        lines.append("|---|---|---|")
        for fid in sorted(by_fund, key=lambda k: -len(by_fund[k])):
            count = len(by_fund[fid])
            cause = (
                "unrecorded DRIPs (cumulative annotation present)"
                if fid in drip_gap_funds
                else "unexplained — needs investigation (perf-fee timing? unrecorded distributions?)"
            )
            lines.append(f"| {fid} | {count} | {cause} |")
        lines.append("")
        lines.append(
            f"_Per-row detail available in `data/canonical/unit_issues.parquet` "
            f"({len(unit_issues)} rows). Top 20 examples below._"
        )
        lines.append("")
        lines.append("| lot_id | as_of | expected | observed | diff |")
        lines.append("|---|---|---|---|---|")
        for issue in unit_issues[:20]:
            lines.append(
                f"| {issue['lot_id']} | {issue['as_of']} | "
                f"{issue['expected']:,.2f} | {issue['observed']:,.2f} | "
                f"{issue['diff']:,.2f} |"
            )
    lines.append("")

    # ---------- 2b. Cumulative DRIP balance vs attested per-event DRIP sum ----------
    drip_gap_rows: list[dict] = []
    if observations_df is not None and not observations_df.empty:
        cum = observations_df[observations_df["observation_type"] == "cumulative_drip_units"].copy()
        # Per-fund attested DRIP total from events (payout_form == 'reinvested').
        if not events_df.empty:
            attested = (
                events_df[
                    (events_df["event_type"] == "dividend")
                    & (events_df["payout_form"] == "reinvested")
                ]
                .groupby("fund_id")["units_delta"]
                .sum()
                .to_dict()
            )
        else:
            attested = {}
        for _, row in cum.iterrows():
            fid = row["fund_id"]
            observed_cum = float(row["value"])
            attested_sum = float(attested.get(fid, 0.0))
            gap = observed_cum - attested_sum
            if abs(gap) > unit_tolerance:
                as_of_val = row["as_of"]
                as_of_iso = (
                    as_of_val.isoformat()
                    if hasattr(as_of_val, "isoformat")
                    else str(as_of_val)
                )
                drip_gap_rows.append(
                    {
                        "fund_id": fid,
                        "source_fund_string": row["source_fund_string"],
                        "as_of": as_of_iso,
                        "cumulative_balance": observed_cum,
                        "attested_per_event_sum": attested_sum,
                        "gap": gap,
                    }
                )

    lines.append("## 2b. Cumulative DRIP balance ↔ attested per-event DRIP sum")
    if observations_df is None or observations_df.empty:
        lines.append("- No cumulative-DRIP observations parsed.")
    else:
        lines.append(
            f"- Funds with cumulative-DRIP observation: {observations_df['fund_id'].nunique()}"
        )
        lines.append(
            f"- Funds with material gap (|cum − Σ attested| > {unit_tolerance}): {len(drip_gap_rows)}"
        )
        if drip_gap_rows:
            lines.append("")
            lines.append("| fund_id | as_of | cumulative_balance | attested_per_event_sum | gap |")
            lines.append("|---|---|---|---|---|")
            for r in drip_gap_rows:
                lines.append(
                    f"| {r['fund_id']} | {r['as_of']} | "
                    f"{r['cumulative_balance']:,.2f} | "
                    f"{r['attested_per_event_sum']:,.2f} | "
                    f"{r['gap']:,.2f} |"
                )
            lines.append("")
            lines.append(
                "> Gap > 0 means DKU records more cumulative DRIP units than we "
                "have per-event annotations for — the missing units are real DRIPs "
                "that were never written into 注释 / 总结 as dated annotations. "
                "Section 2 unit-count mismatches for these funds are explained by this gap."
            )
    lines.append("")

    # ---------- 3. Dividend invariant ----------
    dividend_fails = (
        events_df[
            (events_df.get("event_type") == "dividend")
            & (events_df.get("confidence") == "reconcile_fail")
        ]
        if not events_df.empty
        else pd.DataFrame()
    )
    lines.append("## 3. Dividend invariant (per_unit × eligible ≈ cash)")
    lines.append(f"- Flagged events: {len(dividend_fails)}")
    if len(dividend_fails):
        lines.append("")
        lines.append("| lot_id | event_date | per_unit | eligible | cash | source_locator |")
        lines.append("|---|---|---|---|---|---|")
        for _, row in dividend_fails.iterrows():
            lines.append(
                f"| {row['lot_id']} | {row['event_date']} | "
                f"{row['per_unit_amount']} | {row['eligible_units']} | "
                f"{row['cash_delta']} | {row['source_locator']} |"
            )

    out_path.write_text("\n".join(lines), encoding="utf-8")

    # Persist the per-row unit_issues alongside the report for ad-hoc querying.
    if unit_issues:
        unit_issues_df = pd.DataFrame(unit_issues)
        unit_issues_df["source_id"] = SOURCE_ID
        unit_issues_df["schema_version"] = SCHEMA_VERSION
        unit_issues_df.to_parquet(out_path.parent / "unit_issues.parquet", index=False)

    return {
        "cashflow_issues": cashflow_issues,
        "unit_issues": unit_issues,
        "drip_gap_rows": drip_gap_rows,
        "dividend_fails": len(dividend_fails),
        "report_path": str(out_path),
    }


def apply_data_quality_flags(
    canonical_dir: str | Path,
    reconcile_result: dict,
    *,
    nav_mismatch_fund_ids: set[str] | None = None,
) -> dict:
    """Join reconcile output onto events.parquet + positions.parquet.

    Updates the `data_quality_flag` column in place per the precedence
    in `canonical_contract.resolve_data_quality_flag`.

    `nav_mismatch_fund_ids` is supplied by the consolidator from
    completeness.parquet's `nav_match=False` rows; the master_record
    standalone callsite passes None / empty (no cross-source NAV
    comparison available yet).

    Returns a dict of {table: count_non_clean} for the caller's logging.
    """
    canonical_dir = Path(canonical_dir)
    counts: dict[str, int] = {}

    unit_issue_lots: set[tuple[str, str]] = {
        (issue.get("lot_id"), issue.get("as_of"))
        for issue in reconcile_result.get("unit_issues", [])
        if issue.get("lot_id") is not None
    }
    drip_gap_funds: set[str] = {
        r["fund_id"] for r in reconcile_result.get("drip_gap_rows", []) if r.get("fund_id")
    }
    nav_mismatch_funds: set[str] = nav_mismatch_fund_ids or set()

    # --- events.parquet ---
    events_path = canonical_dir / "events.parquet"
    if events_path.exists():
        events = pd.read_parquet(events_path)
        if "data_quality_flag" not in events.columns:
            events["data_quality_flag"] = "clean"

        def _flag_event(row) -> str:
            candidates: list[str] = []
            if row.get("fund_id") in nav_mismatch_funds:
                candidates.append("nav_mismatch")
            if row.get("confidence") == "reconcile_fail":
                candidates.append("dividend_fail")
            elif row.get("confidence") == "fuzzy":
                candidates.append("confidence_fuzzy")
            if row.get("fund_id") in drip_gap_funds:
                candidates.append("drip_gap")
            return resolve_data_quality_flag(*candidates) if candidates else "clean"

        events["data_quality_flag"] = events.apply(_flag_event, axis=1)
        events.to_parquet(events_path, index=False)
        counts["events"] = int((events["data_quality_flag"] != "clean").sum())

    # --- positions.parquet ---
    positions_path = canonical_dir / "positions.parquet"
    if positions_path.exists():
        positions = pd.read_parquet(positions_path)
        if "data_quality_flag" not in positions.columns:
            positions["data_quality_flag"] = "clean"

        def _flag_position(row) -> str:
            candidates: list[str] = []
            as_of_str = (
                row["as_of"].isoformat()
                if hasattr(row.get("as_of"), "isoformat")
                else str(row.get("as_of"))
            )
            if row.get("fund_id") in nav_mismatch_funds:
                candidates.append("nav_mismatch")
            if (row.get("lot_id"), as_of_str) in unit_issue_lots:
                candidates.append("unit_mismatch")
            if row.get("fund_id") in drip_gap_funds:
                candidates.append("drip_gap")
            return resolve_data_quality_flag(*candidates) if candidates else "clean"

        positions["data_quality_flag"] = positions.apply(_flag_position, axis=1)
        positions.to_parquet(positions_path, index=False)
        counts["positions"] = int((positions["data_quality_flag"] != "clean").sum())

    return counts
