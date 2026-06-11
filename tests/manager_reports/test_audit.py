"""Tests for pdf.audit — numeric cross-check against pdfplumber output."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from vector_flow_connect.manager_reports.audit import (
    NumericDiscrepancy,
    audit_numeric_fields,
)

# RUIYUAN_PDF lives in the dkup scratch repo (gitignored fund-manager
# PDFs); soft-skip when absent so vfc CI passes without DKU-side
# artifacts. Matches the dkup `test_end_to_end_ruiyuan.py` skip pattern.
DKUP_REPO = Path(__file__).resolve().parents[3].parent / "dku_prototyping"
RUIYUAN_PDF = (
    DKUP_REPO / "sample_inputs" / "20260430 私募月报" / "睿远基金-睿见1号-投资月报(2026-04-30).pdf"
)
FIXTURE = Path(__file__).parent / "fixtures" / "ruiyuan_2026_04_30_payload.json"


@pytest.fixture
def ruiyuan_payload() -> dict:
    return json.loads(FIXTURE.read_text())["payload"]


def test_audit_returns_list_of_discrepancies(ruiyuan_payload):
    """The audit machinery runs end-to-end and produces structured output."""
    if not RUIYUAN_PDF.exists():
        pytest.skip(f"PDF fixture absent (dkup scratch repo): {RUIYUAN_PDF}")
    discrepancies = audit_numeric_fields(ruiyuan_payload, RUIYUAN_PDF)
    assert isinstance(discrepancies, list)
    for d in discrepancies:
        assert isinstance(d, NumericDiscrepancy)
        assert d.field_path
        assert isinstance(d.expected_value, float)


def test_audit_discrepancies_under_10pct_of_fields(ruiyuan_payload):
    """Real LLM extraction is allowed to have a few errors but not many.

    This is a soft regression test: if the discrepancy rate spikes, either
    the prompt has regressed, the tolerance is too tight, or the PDF format
    changed. Discrepancies above this threshold should fail loud.
    """
    if not RUIYUAN_PDF.exists():
        pytest.skip(f"PDF fixture absent (dkup scratch repo): {RUIYUAN_PDF}")
    discrepancies = audit_numeric_fields(ruiyuan_payload, RUIYUAN_PDF)
    # Count payload-level numeric fields we're auditing — roughly nav + sir +
    # 89 monthly_returns + ~16 sectors → ~107. 10% threshold ≈ 10 errors.
    assert len(discrepancies) < 15, (
        f"too many numeric discrepancies ({len(discrepancies)}): "
        f"prompt regression or PDF format change?"
    )


def test_audit_flags_fabricated_number(ruiyuan_payload):
    # Mutate the payload — replace the NAV with a distinctive number that
    # does NOT appear in the PDF anywhere (the real NAV is 2.7578; use 777.77
    # which is far from every value in the report).
    if not RUIYUAN_PDF.exists():
        pytest.skip(f"PDF fixture absent (dkup scratch repo): {RUIYUAN_PDF}")
    mutated = copy.deepcopy(ruiyuan_payload)
    mutated["nav_per_unit"] = 777.77
    discrepancies = audit_numeric_fields(mutated, RUIYUAN_PDF)
    paths = [d.field_path for d in discrepancies]
    assert "nav_per_unit" in paths, f"expected nav_per_unit flagged, got {paths}"


def test_audit_flags_fabricated_monthly_return(ruiyuan_payload):
    if not RUIYUAN_PDF.exists():
        pytest.skip(f"PDF fixture absent (dkup scratch repo): {RUIYUAN_PDF}")
    mutated = copy.deepcopy(ruiyuan_payload)
    if not mutated.get("monthly_returns"):
        pytest.skip("fixture has no monthly_returns to mutate")
    mutated["monthly_returns"][0]["return_pct"] = 6543.21
    discrepancies = audit_numeric_fields(mutated, RUIYUAN_PDF)
    paths = [d.field_path for d in discrepancies]
    assert any(p.startswith("monthly_returns[0]") for p in paths), (
        f"expected monthly_returns[0] flagged, got {paths}"
    )
