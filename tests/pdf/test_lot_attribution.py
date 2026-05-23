"""Tests for pdf.lot_attribution — pro-rata split of fund-level events."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from vector_flow_connect.pdf.lot_attribution import (
    _allocate,
    _open_lots_at,
    pro_rata_split,
)


def _positions(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _raw_event(**kwargs) -> dict:
    """Minimal raw event with required fields. Override via kwargs."""
    base = {
        "event_type": "perf_fee",
        "fund_id": "fnd_test",
        "lot_id": None,
        "event_date": date(2025, 9, 30),
        "units_delta": -1234.56,
        "cash_delta": None,
        "source_artifact_hash": "deadbeef" * 8,
        "confidence": "clean",
        "notes_raw": "已累计扣除1,234.56份额作为业绩报酬",
    }
    base.update(kwargs)
    return base


# -------------------- _allocate --------------------


def test_allocate_three_equal_weights_splits_evenly():
    out = _allocate(300.0, [1.0, 1.0, 1.0], decimals=2)
    assert out == [100.0, 100.0, 100.0]


def test_allocate_residual_goes_to_largest_weight():
    out = _allocate(100.0, [3.0, 3.0, 3.0], decimals=2)
    # 100 / 3 = 33.33 each → sum 99.99, residual 0.01 to (first) largest
    assert out == [33.34, 33.33, 33.33]
    assert sum(out) == 100.0


def test_allocate_unequal_weights():
    out = _allocate(1000.0, [2.0, 3.0, 5.0], decimals=2)
    assert out == [200.0, 300.0, 500.0]
    assert sum(out) == 1000.0


def test_allocate_zero_total_returns_empty_or_zeros():
    # Edge: zero delta with weights
    out = _allocate(0.0, [1.0, 2.0], decimals=2)
    assert sum(out) == 0.0


# -------------------- _open_lots_at --------------------


def test_open_lots_picks_latest_snapshot_per_lot():
    pos = _positions(
        [
            {"lot_id": "lot_A", "fund_id": "fnd_test", "as_of": date(2025, 1, 31), "units": 100.0},
            {"lot_id": "lot_A", "fund_id": "fnd_test", "as_of": date(2025, 6, 30), "units": 100.0},
            {
                "lot_id": "lot_A",
                "fund_id": "fnd_test",
                "as_of": date(2026, 4, 30),
                "units": 80.0,
            },  # post-event
            {"lot_id": "lot_B", "fund_id": "fnd_test", "as_of": date(2025, 6, 30), "units": 50.0},
        ]
    )
    open_lots = _open_lots_at(pos, fund_id="fnd_test", event_date=date(2025, 9, 30))
    assert len(open_lots) == 2
    # Should pick the 2025-06-30 snapshot for lot_A (latest ≤ 2025-09-30), NOT 2026-04-30
    lot_a = open_lots[open_lots["lot_id"] == "lot_A"].iloc[0]
    assert lot_a["as_of"] == date(2025, 6, 30)
    assert lot_a["units"] == 100.0


def test_open_lots_filters_units_zero():
    pos = _positions(
        [
            {
                "lot_id": "lot_closed",
                "fund_id": "fnd_test",
                "as_of": date(2025, 6, 30),
                "units": 0.0,
            },
            {
                "lot_id": "lot_open",
                "fund_id": "fnd_test",
                "as_of": date(2025, 6, 30),
                "units": 100.0,
            },
        ]
    )
    open_lots = _open_lots_at(pos, fund_id="fnd_test", event_date=date(2025, 9, 30))
    assert list(open_lots["lot_id"]) == ["lot_open"]


def test_open_lots_filters_other_funds():
    pos = _positions(
        [
            {"lot_id": "lot_A", "fund_id": "fnd_test", "as_of": date(2025, 6, 30), "units": 100.0},
            {"lot_id": "lot_X", "fund_id": "fnd_other", "as_of": date(2025, 6, 30), "units": 999.0},
        ]
    )
    open_lots = _open_lots_at(pos, fund_id="fnd_test", event_date=date(2025, 9, 30))
    assert list(open_lots["lot_id"]) == ["lot_A"]


# -------------------- pro_rata_split --------------------


def test_split_single_lot_inherits_confidence():
    pos = _positions(
        [
            {
                "lot_id": "lot_solo",
                "fund_id": "fnd_test",
                "as_of": date(2025, 6, 30),
                "units": 100.0,
            },
        ]
    )
    splits, issue = pro_rata_split(_raw_event(), pos)
    assert issue is None
    assert len(splits) == 1
    s = splits[0]
    assert s["lot_id"] == "lot_solo"
    assert s["units_delta"] == -1234.56
    assert s["confidence"] == "clean"  # inherited (no ambiguity)
    assert "[pro_rata" not in s["notes_raw"]  # no annotation when singleton
    assert s["event_id"].startswith("evt_")


def test_split_three_equal_lots_evenly():
    pos = _positions(
        [
            {"lot_id": "lot_A", "fund_id": "fnd_test", "as_of": date(2025, 6, 30), "units": 100.0},
            {"lot_id": "lot_B", "fund_id": "fnd_test", "as_of": date(2025, 6, 30), "units": 100.0},
            {"lot_id": "lot_C", "fund_id": "fnd_test", "as_of": date(2025, 6, 30), "units": 100.0},
        ]
    )
    splits, issue = pro_rata_split(_raw_event(units_delta=-300.0), pos)
    assert issue is None
    assert len(splits) == 3
    assert sum(s["units_delta"] for s in splits) == -300.0
    assert all(s["units_delta"] == -100.0 for s in splits)
    assert all(s["confidence"] == "fuzzy" for s in splits)
    assert all("[pro_rata" in s["notes_raw"] for s in splits)


def test_split_three_unequal_lots_proportional():
    """Use 睿远's real lot proportions."""
    pos = _positions(
        [
            {
                "lot_id": "lot_e5",
                "fund_id": "fnd_6b21fcc7",
                "as_of": date(2025, 9, 30),
                "units": 1390949.55,
            },
            {
                "lot_id": "lot_f9",
                "fund_id": "fnd_6b21fcc7",
                "as_of": date(2025, 9, 30),
                "units": 399281.29,
            },
            {
                "lot_id": "lot_f2",
                "fund_id": "fnd_6b21fcc7",
                "as_of": date(2025, 9, 30),
                "units": 581643.34,
            },
        ]
    )
    raw = _raw_event(fund_id="fnd_6b21fcc7", units_delta=-2371.87)
    splits, issue = pro_rata_split(raw, pos)
    assert issue is None
    assert len(splits) == 3
    total = sum(s["units_delta"] for s in splits)
    assert total == pytest.approx(-2371.87, abs=1e-4)
    # Largest lot gets largest absolute share
    by_lot = {s["lot_id"]: s["units_delta"] for s in splits}
    assert abs(by_lot["lot_e5"]) > abs(by_lot["lot_f2"]) > abs(by_lot["lot_f9"])


def test_split_with_no_open_lots_returns_issue():
    pos = _positions(
        [
            {
                "lot_id": "lot_late",
                "fund_id": "fnd_test",
                "as_of": date(2026, 1, 31),
                "units": 100.0,
            },
        ]
    )
    splits, issue = pro_rata_split(_raw_event(), pos)
    assert splits == []
    assert issue is not None
    assert issue["reason"] == "no_open_lots_at_event_date"
    assert issue["fund_id"] == "fnd_test"


def test_split_event_id_is_deterministic_across_runs():
    pos = _positions(
        [
            {"lot_id": "lot_A", "fund_id": "fnd_test", "as_of": date(2025, 6, 30), "units": 100.0},
            {"lot_id": "lot_B", "fund_id": "fnd_test", "as_of": date(2025, 6, 30), "units": 200.0},
        ]
    )
    s1, _ = pro_rata_split(_raw_event(), pos)
    s2, _ = pro_rata_split(_raw_event(), pos)
    ids_1 = sorted(s["event_id"] for s in s1)
    ids_2 = sorted(s["event_id"] for s in s2)
    assert ids_1 == ids_2


def test_split_cash_event_distributes_by_units():
    pos = _positions(
        [
            {"lot_id": "lot_A", "fund_id": "fnd_test", "as_of": date(2025, 6, 30), "units": 100.0},
            {"lot_id": "lot_B", "fund_id": "fnd_test", "as_of": date(2025, 6, 30), "units": 300.0},
        ]
    )
    raw = _raw_event(
        event_type="dividend",
        units_delta=None,
        cash_delta=4000.0,
    )
    splits, issue = pro_rata_split(raw, pos)
    assert issue is None
    by_lot = {s["lot_id"]: s["cash_delta"] for s in splits}
    assert by_lot["lot_A"] == 1000.0  # 1/4 of 4000
    assert by_lot["lot_B"] == 3000.0
    assert all(s["units_delta"] is None for s in splits)
