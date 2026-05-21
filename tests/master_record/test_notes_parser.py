"""Tests for the regex extractors in notes_parser.py."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from vector_flow_connect.master_record import notes_parser as np
from vector_flow_connect.master_record.canonical import lot_id

LOT_CTX = {
    "lot_id": lot_id("fnd_test", date(2024, 1, 15), 100_000.0),
    "fund_id": "fnd_test",
    "source_fund_string": "某基金 (000000)",
    "units_at_lot": 50_000.0,
}

COMMON = {
    "lot_context": LOT_CTX,
    "source_locator": "20260430:R7",
    "source_artifact": "留本基金动态资产配置情况.xlsx",
    "source_artifact_hash": "deadbeef",
    "recorded_at": date(2026, 4, 30),
    "extracted_at": datetime(2026, 5, 18, tzinfo=timezone.utc),
}


# ---------- Redemption ----------


def test_full_redemption_chinese_comma():
    text = "于2025年1月15日全部赎回，赎回净值1.5043"
    events = np.parse(text, **COMMON)
    assert len(events) == 1
    e = events[0]
    assert e["event_type"] == "redemption"
    assert e["event_date"] == date(2025, 1, 15)
    assert e["per_unit_amount"] == pytest.approx(1.5043)
    assert e["units_delta"] == pytest.approx(-50_000.0)
    assert e["cash_delta"] == pytest.approx(50_000.0 * 1.5043)
    assert e["confidence"] == "clean"
    assert e["lot_id"] == LOT_CTX["lot_id"]


def test_full_redemption_ascii_comma():
    text = "于2025年1月15日全部赎回,赎回净值1.5043"
    events = np.parse(text, **COMMON)
    assert len(events) == 1


def test_full_redemption_period_and_alt_term():
    # Real workbook case: 20241231!R10
    text = "于2025年1月9日全部赎回。赎回日净额1.9905"
    events = np.parse(text, **COMMON)
    assert len(events) == 1
    e = events[0]
    assert e["event_type"] == "redemption"
    assert e["event_date"] == date(2025, 1, 9)
    assert e["per_unit_amount"] == pytest.approx(1.9905)


# ---------- Dividend (simple) ----------


def test_dividend_simple_clean():
    text = "实际收益包含2024年6月每单位分红0.05元，分红份额100000，合计收到的现金红利5000元"
    events = np.parse(text, **COMMON)
    assert len(events) == 1
    e = events[0]
    assert e["event_type"] == "dividend"
    assert e["payout_form"] == "cash"
    assert e["event_date"] == date(2024, 6, 30)
    assert e["per_unit_amount"] == pytest.approx(0.05)
    assert e["eligible_units"] == pytest.approx(100_000.0)
    assert e["cash_delta"] == pytest.approx(5000.0)
    assert e["confidence"] == "clean"


def test_dividend_invariant_violation_flagged():
    text = "2024年6月每单位分红0.05元，分红份额100000，合计收到的现金红利9999元"
    events = np.parse(text, **COMMON)
    assert events[0]["confidence"] == "reconcile_fail"


def test_dividend_simple_thousand_separators():
    text = "2025年1月每单位分红0.04元，分红份额308,515.14份,合计收到的现金红利12,340.61元"
    events = np.parse(text, **COMMON)
    assert len(events) == 1
    e = events[0]
    assert e["eligible_units"] == pytest.approx(308515.14)
    assert e["cash_delta"] == pytest.approx(12340.61)


# ---------- Dividend (multi-period) ----------


def test_dividend_multi_period_flagged_fuzzy():
    # Real workbook case: 20230824!Q4
    text = (
        "（实际收益包含2021和2022年每单位分红 0.2470元和0.0780元，"
        "分红总份额为493,490.59份,合计收到的现金红利160,385.05元）"
    )
    events = np.parse(text, **COMMON)
    # Expect exactly one event — multi-period collapses to a single
    # aggregate event, not duplicated by the simple pattern.
    assert len(events) == 1
    e = events[0]
    assert e["event_type"] == "dividend"
    assert e["payout_form"] == "cash"
    assert e["confidence"] == "fuzzy"
    assert e["cash_delta"] == pytest.approx(160_385.05)
    assert e["eligible_units"] == pytest.approx(493_490.59)


# ---------- DRIP ----------


def test_drip_basic():
    # Real workbook case: 20201215!L6
    text = "买入价1.2480元，截至12月14日净值1.197元。2020年12月8日，红利再投资18821.92份，单位净值1.196元。"
    events = np.parse(text, **COMMON)
    assert len(events) == 1
    e = events[0]
    assert e["event_type"] == "dividend"
    assert e["payout_form"] == "reinvested"
    assert e["event_date"] == date(2020, 12, 8)
    assert e["units_delta"] == pytest.approx(18821.92)
    assert e["per_unit_amount"] == pytest.approx(1.196)
    assert e["cash_delta"] is None


# ---------- Performance fee ----------


def test_perf_fee_basic():
    text = "（已累计扣除65,207份额作为业绩报酬）"
    events = np.parse(text, **COMMON)
    assert len(events) == 1
    e = events[0]
    assert e["event_type"] == "perf_fee"
    assert e["units_delta"] == pytest.approx(-65_207)
    assert e["confidence"] == "fuzzy"


def test_perf_fee_decimal_units():
    text = "已累计扣除123.45份额作为业绩报酬"
    events = np.parse(text, **COMMON)
    assert events[0]["units_delta"] == pytest.approx(-123.45)


# ---------- Composition / hygiene ----------


def test_multi_pattern_single_cell():
    text = (
        "于2025年1月15日全部赎回，赎回净值1.5043；"
        "实际收益包含2024年12月每单位分红0.02元，"
        "分红份额50000，合计收到的现金红利1000元"
    )
    events = np.parse(text, **COMMON)
    assert {e["event_type"] for e in events} == {"redemption", "dividend"}


def test_empty_input():
    assert np.parse("", **COMMON) == []
    assert np.parse(None, **COMMON) == []


def test_event_ids_stable_across_reads():
    text = "于2025年1月15日全部赎回，赎回净值1.5043"
    e1 = np.parse(text, **COMMON)[0]
    e2 = np.parse(text, **COMMON)[0]
    assert e1["event_id"] == e2["event_id"]


def test_drip_and_simple_dividend_dont_double_count():
    # A note that has BOTH a DRIP entry and a simple cash dividend
    # mention — should be two events of different shapes, not overlap.
    text = (
        "2025年1月每单位分红0.04元，分红份额100000，合计收到的现金红利4000元。"
        "2025年2月10日，红利再投资1234.56份，单位净值1.20元。"
    )
    events = np.parse(text, **COMMON)
    assert len(events) == 2
    forms = {e["payout_form"] for e in events}
    assert forms == {"cash", "reinvested"}
