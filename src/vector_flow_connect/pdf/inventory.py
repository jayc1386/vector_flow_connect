"""Walk `sample_inputs/YYYYMMDD 私募月报/` and enumerate PDFs.

The parent directory name carries the reliable period-end date
(`YYYYMMDD 私募月报`). Filenames vary wildly across managers, so we
extract a best-effort `fund_filename_token` for grouping/filtering but
do not rely on it for fund identity — the LLM emits the canonical
`fund_name_zh` from the PDF header.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pandas as pd

_DIR_PATTERN = re.compile(r"^(?P<ymd>\d{8})\s*私募月报$")
_MULTIFUND_HINT = re.compile(r"合集|compilation|consolidated", re.IGNORECASE)


def _parse_period_end(dirname: str) -> date | None:
    m = _DIR_PATTERN.match(dirname.strip())
    if not m:
        return None
    ymd = m["ymd"]
    return date(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:8]))


def _fund_filename_token(filename: str) -> str:
    """First Chinese-word run before a separator. Empty if not recognisable."""
    stem = Path(filename).stem
    # Strip leading numeric / underscore prefix (`1_睿远...`)
    stem = re.sub(r"^\d+[_\s-]*", "", stem)
    # Take everything up to the first occurrence of a year/date/separator.
    m = re.match(r"^([一-鿿]+)", stem)
    return m.group(1) if m else ""


def scan(root: str | Path) -> pd.DataFrame:
    """Return one row per PDF under `root/YYYYMMDD 私募月报/*.pdf`.

    Columns: pdf_path, parent_dir, period_end, filename, fund_filename_token.
    """
    root = Path(root)
    rows: list[dict] = []
    for child in sorted(root.iterdir()) if root.exists() else ():
        if not child.is_dir():
            continue
        period_end = _parse_period_end(child.name)
        if period_end is None:
            continue
        for pdf in sorted(child.glob("*.pdf")):
            rows.append(
                {
                    "pdf_path": str(pdf),
                    "parent_dir": child.name,
                    "period_end": period_end,
                    "filename": pdf.name,
                    "fund_filename_token": _fund_filename_token(pdf.name),
                    "is_multifund_candidate": bool(_MULTIFUND_HINT.search(pdf.name)),
                }
            )
    return pd.DataFrame(
        rows,
        columns=[
            "pdf_path",
            "parent_dir",
            "period_end",
            "filename",
            "fund_filename_token",
            "is_multifund_candidate",
        ],
    )


def filter_by_token(df: pd.DataFrame, token_substring: str) -> pd.DataFrame:
    """Keep rows whose `fund_filename_token` or filename contains the substring."""
    mask = df["fund_filename_token"].str.contains(token_substring, na=False) | df[
        "filename"
    ].str.contains(token_substring, na=False)
    return df[mask].reset_index(drop=True)
