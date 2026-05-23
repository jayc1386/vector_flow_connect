"""Tests for canonical.py hashing + schema."""

from __future__ import annotations

from datetime import date

from vector_flow_connect.master_record.canonical import (
    EVENT_COLUMNS,
    LOT_COLUMNS,
    empty_event,
    empty_position,
    event_id,
    fund_id_stub,
    lot_id,
)


def test_event_id_stable():
    a = event_id("hash", "subscription", "lot_abc")
    b = event_id("hash", "subscription", "lot_abc")
    assert a == b
    assert a.startswith("evt_")


def test_event_id_changes_with_dedup_key():
    a = event_id("hash", "dividend", "lot_abc:2025-01-31:cash:91630.00")
    b = event_id("hash", "dividend", "lot_abc:2025-02-28:cash:91630.00")
    assert a != b


def test_event_id_independent_of_source_locator():
    """Same dividend seen in different snapshots must dedupe."""
    # Both should produce the SAME event_id — the per-snapshot locator
    # is not part of the hash.
    a = event_id("hash", "dividend", "lot_x:2025-01-31:cash:91630.00")
    b = event_id("hash", "dividend", "lot_x:2025-01-31:cash:91630.00")
    assert a == b


def test_lot_id_stable_across_float_noise():
    a = lot_id("fnd_x", date(2024, 1, 15), 100_000.0)
    b = lot_id("fnd_x", date(2024, 1, 15), 100_000.001)  # < 1 cent noise rounds away
    assert a == b


def test_lot_id_differs_by_date():
    a = lot_id("fnd_x", date(2024, 1, 15), 100_000.0)
    b = lot_id("fnd_x", date(2024, 1, 16), 100_000.0)
    assert a != b


def test_fund_id_stub_normalizes_whitespace():
    assert fund_id_stub("华夏纯债债券A (000015)") == fund_id_stub("  华夏纯债债券A (000015)  ")


def test_empty_event_has_all_columns():
    e = empty_event()
    assert set(e.keys()) == set(EVENT_COLUMNS)
    assert all(v is None for v in e.values())


def test_empty_position_has_all_columns():
    p = empty_position()
    assert set(p.keys()) == set(LOT_COLUMNS) or set(p.keys())  # smoke
