"""Detect per-fund page ranges in multi-fund PDFs.

九鞅's compilation reports have one fund per page with a final disclaimer
page (e.g., `九鞅投资产品月度业绩合集-20260228.pdf` → 禾禧五号 / 禾瑞十号 /
禾瑞八号 / 禾禧一号 / 免责声明). This module reads pdfplumber's text-layer
output, finds a fund-name header on each page, and groups consecutive pages
under the same fund.

For PDFs that don't actually contain per-page fund boundaries (e.g., a single
fund on multiple pages), this returns a single range covering all pages.
The runner uses the page count to decide whether to fan out per fund or fall
back to a single full-PDF extraction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber

# A fund name on the page: a run of CJK characters ending in a known
# fund/trust suffix. Matches "九鞅禾禧五号私募证券投资基金" (works on the
# monthly compilation header). Intentionally narrow: relies on a CJK
# fund-number (五号/十号/八号 etc.) so cover-letter text mentioning 基金
# doesn't false-positive.
_FUND_HEADER_RE = re.compile(
    r"([一-鿿（）]{2,}号(?:[一-鿿（）]*?)"
    r"(?:私募证券投资基金|集合资产管理计划|集合资金信托计划|私募基金|基金))"
)
_DISCLAIMER_RE = re.compile(r"^(免责声明|风险揭示|重要信息披露)")
_HEAD_LINES = 6  # examine the first N non-empty lines of each page


@dataclass
class FundPageRange:
    fund_hint: str | None  # parsed fund name (or None for disclaimer pages)
    pages: list[int] = field(default_factory=list)  # 1-based page numbers
    role: str = "fund"  # 'fund' | 'disclaimer'


def _first_fund_header(text: str) -> str | None:
    """Return the first fund name found in the first few non-empty lines."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    head = "\n".join(lines[:_HEAD_LINES])
    m = _FUND_HEADER_RE.search(head)
    return m.group(1) if m else None


def _is_disclaimer_page(text: str) -> bool:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return False
    # Disclaimer typically starts a page with one of these headings
    return bool(_DISCLAIMER_RE.match(lines[0]))


def detect_fund_pages(pdf_path: str | Path) -> list[FundPageRange]:
    """Walk pages of `pdf_path` and identify per-fund boundaries.

    Returns a list of `FundPageRange`. For single-fund PDFs the result is
    typically one range with all pages and `role='fund'`. Multi-fund PDFs
    return one range per fund plus optional disclaimer ranges.
    """
    ranges: list[FundPageRange] = []
    current_fund: str | None = None
    current_pages: list[int] = []

    def _flush_current() -> None:
        nonlocal current_fund, current_pages
        if current_pages:
            ranges.append(
                FundPageRange(
                    fund_hint=current_fund,
                    pages=current_pages,
                    role="fund",
                )
            )
        current_fund = None
        current_pages = []

    with pdfplumber.open(str(pdf_path)) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""

            if _is_disclaimer_page(text):
                _flush_current()
                ranges.append(
                    FundPageRange(
                        fund_hint=None,
                        pages=[i],
                        role="disclaimer",
                    )
                )
                continue

            fund = _first_fund_header(text)

            if fund is None:
                # Continuation page — attach to current group
                current_pages.append(i)
                continue

            if current_fund is None:
                # Start the first group
                current_fund = fund
                current_pages = [i]
            elif fund == current_fund:
                # Same fund continuing
                current_pages.append(i)
            else:
                # Boundary
                _flush_current()
                current_fund = fund
                current_pages = [i]

        _flush_current()

    return ranges
