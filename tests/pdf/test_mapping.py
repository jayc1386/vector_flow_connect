"""Tests for pdf.mapping — payload → canonical observations + raw_events."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from vector_flow_connect.pdf.canonical import (
    EVENT_COLUMNS,
    EXTRACTOR_NAME,
    EXTRACTOR_VERSION,
    OBSERVATION_COLUMNS,
    SCHEMA_VERSION,
)
from vector_flow_connect.pdf.mapping import payload_to_canonical

FIXTURE = Path(__file__).parent / "fixtures" / "ruiyuan_2026_04_30_payload.json"


@pytest.fixture
def ruiyuan_payload() -> dict:
    return json.loads(FIXTURE.read_text())["payload"]


@pytest.fixture
def source_ctx() -> dict:
    return {
        "fund_id": "fnd_6b21fcc7",
        "source_fund_string": "睿远基金睿见1号",
        "source_artifact": "睿远基金-睿见1号-投资月报(2026-04-30).pdf",
        "source_artifact_hash": "deadbeef" * 8,
        "extracted_at": datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc),
    }


def test_payload_to_canonical_returns_three_keys(ruiyuan_payload, source_ctx):
    out = payload_to_canonical(ruiyuan_payload, **source_ctx)
    assert set(out.keys()) == {"observations", "raw_events", "dropped"}
    assert isinstance(out["observations"], list)
    assert isinstance(out["raw_events"], list)


def test_clean_fund_has_no_events(ruiyuan_payload, source_ctx):
    # 睿远 v1: events should be empty for this clean fund.
    out = payload_to_canonical(ruiyuan_payload, **source_ctx)
    assert out["raw_events"] == []
    assert out["dropped"] == []


def test_observations_match_canonical_schema(ruiyuan_payload, source_ctx):
    out = payload_to_canonical(ruiyuan_payload, **source_ctx)
    assert out["observations"], "expected at least nav + monthly_returns observations"
    for row in out["observations"]:
        assert set(row.keys()) == set(OBSERVATION_COLUMNS), (
            f"observation has extra/missing keys: {set(row.keys()) ^ set(OBSERVATION_COLUMNS)}"
        )


def test_nav_observation_present(ruiyuan_payload, source_ctx):
    out = payload_to_canonical(ruiyuan_payload, **source_ctx)
    navs = [o for o in out["observations"] if o["observation_type"] == "nav_per_unit"]
    assert len(navs) == 1
    assert navs[0]["value"] == pytest.approx(2.7578)
    assert navs[0]["as_of"] == date(2026, 4, 30)
    assert navs[0]["source_locator"] == "pdf:page=1"


def test_monthly_returns_become_observations(ruiyuan_payload, source_ctx):
    out = payload_to_canonical(ruiyuan_payload, **source_ctx)
    mrs = [o for o in out["observations"] if o["observation_type"] == "monthly_return_pct"]
    assert len(mrs) >= 80, f"expected >= 80 monthly returns, got {len(mrs)}"
    # Every as_of should be a month-end date.
    for o in mrs:
        assert isinstance(o["as_of"], date)
        assert o["as_of"].day in {28, 29, 30, 31}


def test_sector_observations_carry_label_as_key(ruiyuan_payload, source_ctx):
    """Per prism canonical_provenance v1.0.0, sub-keyed observations lift
    the sub-key (sector label) from notes_raw into the structured `key`
    column + the `source_locator` pointer."""
    out = payload_to_canonical(ruiyuan_payload, **source_ctx)
    sectors = [o for o in out["observations"] if o["observation_type"] == "sector_weight_pct"]
    assert len(sectors) > 0
    for o in sectors:
        assert o["key"] is not None and o["key"].startswith("sector.label=")
        assert "key=sector.label=" in o["source_locator"]


def test_event_mapping_synthetic_perf_fee():
    payload = {
        "fund_name_zh": "Test Fund",
        "report_period_end": "2025-09-30",
        "nav_per_unit": 1.5,
        "extraction_notes": "",
        "events": [
            {
                "event_type": "perf_fee",
                "event_date": "2025-09-30",
                "units_delta": -1234.56,
                "cash_delta": None,
                "per_unit_amount": None,
                "notes_raw": "已累计扣除1,234.56份额作为业绩报酬",
                "confidence_self": "clean",
            },
        ],
    }
    out = payload_to_canonical(
        payload,
        fund_id="fnd_test",
        source_fund_string="Test Fund",
        source_artifact="test.pdf",
        source_artifact_hash="abc",
        extracted_at=datetime(2025, 10, 1, tzinfo=timezone.utc),
    )
    assert len(out["raw_events"]) == 1
    ev = out["raw_events"][0]
    assert ev["event_type"] == "perf_fee"
    assert ev["event_date"] == date(2025, 9, 30)
    assert ev["units_delta"] == -1234.56
    assert ev["confidence"] == "clean"
    assert ev["lot_id"] is None
    assert ev["extractor_name"] == EXTRACTOR_NAME
    assert ev["extractor_version"] == EXTRACTOR_VERSION
    assert ev["schema_version"] == SCHEMA_VERSION
    assert ev["currency"] == "CNY"
    # Should populate every canonical event column.
    assert set(ev.keys()) == set(EVENT_COLUMNS)


def test_event_mapping_distribution_cash():
    payload = {
        "fund_name_zh": "Test Fund",
        "report_period_end": "2025-12-31",
        "nav_per_unit": 1.5,
        "extraction_notes": "",
        "events": [
            {
                "event_type": "distribution_cash",
                "event_date": "2025-12-15",
                "units_delta": None,
                "cash_delta": 45000.0,
                "per_unit_amount": 0.05,
                "notes_raw": "每单位分红0.05元，合计45000元",
                "confidence_self": "clean",
            },
        ],
    }
    out = payload_to_canonical(
        payload,
        fund_id="fnd_test",
        source_fund_string="Test Fund",
        source_artifact="test.pdf",
        source_artifact_hash="abc",
        extracted_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    ev = out["raw_events"][0]
    assert ev["event_type"] == "dividend"
    assert ev["payout_form"] == "cash"
    assert ev["cash_delta"] == 45000.0


def test_unsupported_event_is_dropped():
    payload = {
        "fund_name_zh": "Test Fund",
        "report_period_end": "2025-12-31",
        "nav_per_unit": 1.5,
        "extraction_notes": "",
        "events": [
            {
                "event_type": "subscription_fee",
                "event_date": "2025-12-15",
                "notes_raw": "申购费500元",
                "confidence_self": "clean",
            },
        ],
    }
    out = payload_to_canonical(
        payload,
        fund_id="fnd_test",
        source_fund_string="Test Fund",
        source_artifact="test.pdf",
        source_artifact_hash="abc",
        extracted_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    assert out["raw_events"] == []
    assert len(out["dropped"]) == 1
    assert out["dropped"][0]["reason"] == "unsupported_event_type"
    assert out["dropped"][0]["pdf_event_type"] == "subscription_fee"
