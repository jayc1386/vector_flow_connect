"""Normalize fund names before they enter the resolver.

Strips hyphens, middle-dots, and whitespace separators that fragment
otherwise-identical fund names across sources. Specifically fixes
`睿远基金-睿见1号` (PDF filename / common manager rendering) vs
`睿远基金睿见1号` (master_record spelling) — the resolver's prefix-overlap
heuristic doesn't merge these because of the dash, but the fund is the same.
"""

from __future__ import annotations

import re

_HYPHEN_LIKE = re.compile(r"[\s \-‐-―−－·・∙•‧]+")


def normalize_fund_name(name: str | None) -> str:
    """Remove hyphen-like separators and whitespace from a fund name.

    Preserves all CJK and Latin characters / digits. Idempotent.
    """
    if not name:
        return name or ""
    return _HYPHEN_LIKE.sub("", name.strip())
