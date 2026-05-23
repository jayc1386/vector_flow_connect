"""Tests for pdf.multifund — per-page fund detection in compilation PDFs."""

from __future__ import annotations

from pathlib import Path

import pytest

from vector_flow_connect.pdf.multifund import detect_fund_pages

REPO_ROOT = Path(__file__).resolve().parents[3]
JIUYANG_MONTHLY = (
    REPO_ROOT / "sample_inputs" / "20260228 私募月报" / "九鞅投资产品月度业绩合集-20260228.pdf"
)
JIUYANG_WEEKLY = (
    REPO_ROOT / "sample_inputs" / "20260131 私募月报" / "九鞅投资产品周报合集-20260213.pdf"
)
RUIYUAN_2026_04 = (
    REPO_ROOT / "sample_inputs" / "20260430 私募月报" / "睿远基金-睿见1号-投资月报(2026-04-30).pdf"
)


def _require(path: Path) -> Path:
    if not path.exists():
        pytest.skip(f"sample input missing: {path}")
    return path


def test_detect_jiuyang_monthly_compilation_yields_four_funds():
    ranges = detect_fund_pages(_require(JIUYANG_MONTHLY))
    fund_ranges = [r for r in ranges if r.role == "fund"]
    assert len(fund_ranges) == 4, (
        f"expected 4 fund pages in monthly compilation, got "
        f"{[(r.fund_hint, r.pages) for r in fund_ranges]}"
    )
    # Each fund occupies a single page in the monthly compilation
    for r in fund_ranges:
        assert len(r.pages) == 1, f"expected 1 page per fund, got {r.pages}"
    fund_names = {r.fund_hint for r in fund_ranges}
    assert all("九鞅" in n for n in fund_names), f"unexpected fund names: {fund_names}"


def test_detect_jiuyang_monthly_has_disclaimer_page():
    ranges = detect_fund_pages(_require(JIUYANG_MONTHLY))
    disclaimers = [r for r in ranges if r.role == "disclaimer"]
    assert len(disclaimers) >= 1, "expected at least one disclaimer page"


def test_detect_jiuyang_weekly_falls_back_gracefully():
    """The weekly compilation's page headers don't match the fund-header regex
    (they use a different format ending in 周报). Detector should NOT crash;
    the runner falls back to a single full-PDF extraction in this case."""
    ranges = detect_fund_pages(_require(JIUYANG_WEEKLY))
    # Either one big range or several with no fund hints — both are acceptable;
    # what matters is that <=1 fund-role range has a usable fund_hint, so the
    # runner skips multi-fund fan-out.
    usable = [r for r in ranges if r.role == "fund" and r.fund_hint is not None]
    assert len(usable) <= 1


def test_detect_single_fund_pdf_returns_one_range():
    """睿远 is a 2-page single-fund PDF — detector must NOT split it."""
    ranges = detect_fund_pages(_require(RUIYUAN_2026_04))
    fund_ranges = [r for r in ranges if r.role == "fund"]
    assert len(fund_ranges) == 1
    # All pages folded into one range
    assert fund_ranges[0].pages == [1, 2]
