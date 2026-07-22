"""Incomplete-unit-history exclusion (v0.18.0).

A lot whose units_delta stream carries a NaN (a cost-only/amount-only
subscription the source hasn't priced, or an interim event with unknown
units) has an unanchored cumulative baseline: the prior code dropped the
NaN before summing, so `expected` silently omitted it and the lot
mismatched at every snapshot. Such lots must be excluded from
`unit_issues` and surfaced as `incomplete_unit_lots` instead — never
emitted as false `unit_mismatch` rows.

All rows here are synthetic.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from vector_flow_connect.dku.master_record.reconcile import (
    apply_data_quality_flags,
    reconcile,
)

_EVENT_COLS = [
    "event_id",
    "event_type",
    "fund_id",
    "lot_id",
    "event_date",
    "units_delta",
    "cash_delta",
    "payout_form",
    "confidence",
]


def _events(rows):
    return pd.DataFrame(rows, columns=_EVENT_COLS)


def test_nan_genesis_lot_excluded_and_surfaced(tmp_path):
    """A lot whose opening subscription has NaN units_delta but a later
    priced event (perf-fee) is excluded from unit_issues and reported as
    incomplete — the SEC811 shape."""
    events = _events(
        [
            # cost-only genesis: units unknown at subscription
            ("e1", "subscription", "fnd_x", "lot_a", "2020-01-21", np.nan, -1_710_000.0, None, "clean"),
            # a later priced event that alone would produce a huge false diff
            ("e2", "perf_fee", "fnd_x", "lot_a", "2023-04-21", -65_207.0, np.nan, None, "fuzzy"),
        ]
    )
    positions = pd.DataFrame(
        [
            {"lot_id": "lot_a", "fund_id": "fnd_x", "as_of": "2021-12-31", "units": 1_256_186.0},
            {"lot_id": "lot_a", "fund_id": "fnd_x", "as_of": "2024-12-31", "units": 1_221_868.0},
        ]
    )

    result = reconcile(events, positions, None, out_path=tmp_path / "report.md")

    assert result["unit_issues"] == []
    assert len(result["incomplete_unit_lots"]) == 1
    lot = result["incomplete_unit_lots"][0]
    assert lot["lot_id"] == "lot_a"
    assert lot["fund_id"] == "fnd_x"
    assert lot["first_incomplete_event_date"] == "2020-01-21"
    assert lot["nan_event_count"] == 1
    assert lot["positions_skipped"] == 2


def test_mid_series_nan_on_unit_bearing_event_excluded(tmp_path):
    """A NaN on a non-genesis UNIT-BEARING event (here a later unpriced
    subscription) corrupts the running sum from that point → the lot is
    still excluded."""
    events = _events(
        [
            ("e1", "subscription", "fnd_y", "lot_b", "2020-01-01", 1_000.0, -1_000.0, None, "clean"),
            ("e2", "subscription", "fnd_y", "lot_b", "2020-06-01", np.nan, -500.0, None, "clean"),
        ]
    )
    positions = pd.DataFrame(
        [{"lot_id": "lot_b", "fund_id": "fnd_y", "as_of": "2020-12-31", "units": 1_500.0}]
    )

    result = reconcile(events, positions, None, out_path=tmp_path / "report.md")

    assert result["unit_issues"] == []
    assert [r["lot_id"] for r in result["incomplete_unit_lots"]] == ["lot_b"]


def test_cash_dividend_nan_is_unit_neutral_not_incomplete(tmp_path):
    """A cash dividend (`dividend` + payout_form='cash') carries no units by
    construction, so its absent units_delta means zero — NOT an unknown
    opening position. A lot that reconciled cleanly and merely paid a cash
    dividend must stay clean, never be misclassified incomplete (plan 0120
    fund-accountant F1)."""
    events = _events(
        [
            ("e1", "subscription", "fnd_d", "lot_d", "2020-01-01", 1_000.0, -1_000.0, None, "clean"),
            # cash distribution: no unit dimension, units_delta absent
            ("e2", "dividend", "fnd_d", "lot_d", "2022-12-31", np.nan, 160.0, "cash", "clean"),
        ]
    )
    positions = pd.DataFrame(
        [
            {"lot_id": "lot_d", "fund_id": "fnd_d", "as_of": "2021-12-31", "units": 1_000.0},
            {"lot_id": "lot_d", "fund_id": "fnd_d", "as_of": "2023-12-31", "units": 1_000.0},
        ]
    )

    result = reconcile(events, positions, None, out_path=tmp_path / "report.md")

    assert result["incomplete_unit_lots"] == []
    assert result["unit_issues"] == []


def test_reinvested_dividend_nan_is_a_genuine_gap(tmp_path):
    """A *reinvested* dividend DOES mint units, so a NaN units_delta on it
    is a genuine gap (unlike the cash case) → the lot is excluded."""
    events = _events(
        [
            ("e1", "subscription", "fnd_r", "lot_r", "2020-01-01", 1_000.0, -1_000.0, None, "clean"),
            ("e2", "dividend", "fnd_r", "lot_r", "2022-12-31", np.nan, None, "reinvested", "clean"),
        ]
    )
    positions = pd.DataFrame(
        [{"lot_id": "lot_r", "fund_id": "fnd_r", "as_of": "2023-12-31", "units": 1_050.0}]
    )

    result = reconcile(events, positions, None, out_path=tmp_path / "report.md")

    assert result["unit_issues"] == []
    assert [r["lot_id"] for r in result["incomplete_unit_lots"]] == ["lot_r"]


def test_clean_lot_still_reconciles(tmp_path):
    """A lot with a fully-priced event stream is unaffected: a genuine
    mismatch still surfaces as a unit_issue, and it is NOT listed
    incomplete."""
    events = _events(
        [
            ("e1", "subscription", "fnd_z", "lot_c", "2020-01-01", 1_000.0, -1_000.0, None, "clean"),
        ]
    )
    positions = pd.DataFrame(
        [{"lot_id": "lot_c", "fund_id": "fnd_z", "as_of": "2020-12-31", "units": 1_500.0}]
    )

    result = reconcile(events, positions, None, out_path=tmp_path / "report.md")

    assert result["incomplete_unit_lots"] == []
    assert len(result["unit_issues"]) == 1
    assert result["unit_issues"][0]["lot_id"] == "lot_c"
    assert result["unit_issues"][0]["diff"] == 500.0


def test_apply_flags_tags_incomplete_lot(tmp_path):
    """Positions of an incomplete lot are flagged unit_history_incomplete
    (not unit_mismatch)."""
    positions = pd.DataFrame(
        [
            {"lot_id": "lot_a", "fund_id": "fnd_x", "as_of": "2024-12-31"},
            {"lot_id": "lot_c", "fund_id": "fnd_z", "as_of": "2020-12-31"},
        ]
    )
    positions.to_parquet(tmp_path / "positions.parquet", index=False)

    apply_data_quality_flags(
        tmp_path,
        {
            "unit_issues": [{"lot_id": "lot_c", "as_of": "2020-12-31"}],
            "incomplete_unit_lots": [{"lot_id": "lot_a", "fund_id": "fnd_x"}],
            "drip_gap_rows": [],
        },
    )

    flagged = pd.read_parquet(tmp_path / "positions.parquet").set_index("lot_id")
    assert flagged.loc["lot_a", "data_quality_flag"] == "unit_history_incomplete"
    assert flagged.loc["lot_c", "data_quality_flag"] == "unit_mismatch"
