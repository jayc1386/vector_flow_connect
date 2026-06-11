"""Post-extraction numeric audit.

After the vision LLM returns a payload, pdfplumber extracts every numeric
token from the PDF text layer (which preserves Latin glyphs even when
Chinese is CID-mapped) and we verify each numeric value the model emitted
actually appears somewhere in the PDF. Catches the worst vision-LLM failure
mode — numbers that aren't in the PDF at all.

Does NOT catch swapped numbers (`3.33` read as `3.43` when both exist);
those are caught downstream by NAV ⇄ master_record and monthly-return
self-consistency checks.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pdfplumber

_NUM_TOKEN = re.compile(r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?")
_TOLERANCE = 0.05  # absolute; tight enough to catch hallucinations, lax enough for 1bp rounding


@dataclass
class NumericDiscrepancy:
    field_path: str
    expected_value: float
    nearest_pdf_value: float | None
    nearest_diff: float | None


def _extract_pdf_numbers(pdf_path: str | Path) -> list[float]:
    """Return every numeric token from every page of the PDF as a sorted list of floats."""
    nums: list[float] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            for m in _NUM_TOKEN.finditer(text):
                tok = m.group(0).replace(",", "")
                try:
                    nums.append(float(tok))
                except ValueError:
                    continue
    return sorted(nums)


def _walk_numeric_fields(payload: dict[str, Any]) -> Iterable[tuple[str, float]]:
    """Yield (json-path, value) for every numeric field the model populated.

    Skips fields we don't expect to appear verbatim in pdfplumber output
    (e.g. derived counts, integers that might be table indices).
    """
    if (v := payload.get("nav_per_unit")) is not None:
        yield "nav_per_unit", float(v)
    if (v := payload.get("nav_cumulative")) is not None:
        yield "nav_cumulative", float(v)
    if (v := payload.get("since_inception_return_pct")) is not None:
        yield "since_inception_return_pct", float(v)

    for i, row in enumerate(payload.get("monthly_returns") or []):
        if (v := row.get("return_pct")) is not None:
            yield f"monthly_returns[{i}].return_pct", float(v)

    for i, row in enumerate(payload.get("top_holdings") or []):
        if (w := row.get("weight_pct")) is not None:
            yield f"top_holdings[{i}].weight_pct", float(w)
        if (w := row.get("weight_pct_prior")) is not None:
            yield f"top_holdings[{i}].weight_pct_prior", float(w)

    for i, row in enumerate(payload.get("sector_breakdown") or []):
        if (w := row.get("weight_pct")) is not None:
            yield f"sector_breakdown[{i}].weight_pct", float(w)

    for i, row in enumerate(payload.get("geographic_breakdown") or []):
        if (w := row.get("weight_pct")) is not None:
            yield f"geographic_breakdown[{i}].weight_pct", float(w)

    pc = payload.get("position_counts") or {}
    for k in ("long", "short", "net"):
        if (v := pc.get(k)) is not None:
            yield f"position_counts.{k}", float(v)

    for i, evt in enumerate(payload.get("events") or []):
        for key in ("units_delta", "cash_delta", "per_unit_amount"):
            if (v := evt.get(key)) is not None:
                yield f"events[{i}].{key}", float(v)


def _nearest(value: float, pool: list[float]) -> tuple[float | None, float | None]:
    if not pool:
        return None, None
    best = min(pool, key=lambda x: abs(x - value))
    return best, abs(best - value)


def audit_numeric_fields(
    payload: dict[str, Any],
    pdf_path: str | Path,
    *,
    tolerance: float = _TOLERANCE,
) -> list[NumericDiscrepancy]:
    """Return one discrepancy per numeric payload field whose value is not
    present in the PDF text layer (within `tolerance`)."""
    pdf_numbers = _extract_pdf_numbers(pdf_path)
    discrepancies: list[NumericDiscrepancy] = []
    for path, value in _walk_numeric_fields(payload):
        nearest, diff = _nearest(value, pdf_numbers)
        if diff is None or diff > tolerance:
            discrepancies.append(
                NumericDiscrepancy(
                    field_path=path,
                    expected_value=value,
                    nearest_pdf_value=nearest,
                    nearest_diff=diff,
                )
            )
    return discrepancies
