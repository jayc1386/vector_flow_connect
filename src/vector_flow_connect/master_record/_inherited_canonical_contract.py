"""Cross-adapter canonical-contract enums + validators.

Mirrors prism's `docs/contracts/canonical_provenance.md` v1.0.0
shape on the dkup side. Used by `master_record.canonical`,
`extraction.pdf.canonical`, and `reconcile` to constrain
free-text columns at write time.

Pure-Python; no I/O.
"""

from __future__ import annotations

from typing import Literal

AssetClass = Literal["Growth", "Fixed Income", "Diversifiers", "Inflation Sensitive"]
ASSET_CLASS_VALUES: frozenset[str] = frozenset(AssetClass.__args__)  # type: ignore[attr-defined]

# Dividend events only. `None` is valid (used by every non-dividend event_type).
# Both Path A (events_sheet) and Path B (notes_parser) must honor this enum so
# prism's `record_cash_flow.flow_type` mapping works uniformly:
#   dividend + cash       → distribution
#   dividend + reinvested → drip
#   perf_fee + None       → performance_fee
PayoutForm = Literal["cash", "reinvested"]
PAYOUT_FORM_VALUES: frozenset[str] = frozenset(PayoutForm.__args__)  # type: ignore[attr-defined]

DataQualityFlag = Literal[
    "clean",
    "derived_from_events_log",
    "derived_from_notes",
    "unit_mismatch",
    "nav_mismatch",
    "cash_share_mismatch",
    "dividend_fail",
    "drip_gap",
    "confidence_fuzzy",
]
DATA_QUALITY_FLAG_VALUES: frozenset[str] = frozenset(DataQualityFlag.__args__)  # type: ignore[attr-defined]

# Precedence when a row matches multiple issue classes — higher wins.
# `clean` is the default and lowest precedence. Derivation lineage flags
# (`derived_from_*`) live just above `clean` — they describe the parse
# path, not a data-quality issue, so any real issue (cash_share_mismatch,
# unit_mismatch, ...) outranks them.
_DATA_QUALITY_PRECEDENCE: dict[str, int] = {
    "clean": 0,
    "derived_from_events_log": 1,
    "derived_from_notes": 2,
    "confidence_fuzzy": 3,
    "dividend_fail": 4,
    "drip_gap": 5,
    "cash_share_mismatch": 6,
    "unit_mismatch": 7,
    "nav_mismatch": 8,
}


def validate_asset_class(value: str) -> str:
    if value not in ASSET_CLASS_VALUES:
        raise ValueError(f"asset_class={value!r} not in IPS enum {sorted(ASSET_CLASS_VALUES)}")
    return value


def validate_payout_form(value: str | None) -> str | None:
    """Returns the value if valid; raises on a bad string. `None` is OK
    (every non-dividend event_type carries `payout_form=None`)."""
    if value is None:
        return None
    if value not in PAYOUT_FORM_VALUES:
        raise ValueError(
            f"payout_form={value!r} not in enum {sorted(PAYOUT_FORM_VALUES)} "
            f"(or None for non-dividend events)"
        )
    return value


def validate_data_quality_flag(value: str) -> str:
    if value not in DATA_QUALITY_FLAG_VALUES:
        raise ValueError(
            f"data_quality_flag={value!r} not in enum {sorted(DATA_QUALITY_FLAG_VALUES)}"
        )
    return value


def resolve_data_quality_flag(*candidates: str) -> str:
    """Pick the highest-precedence flag among candidates.

    Caller passes every issue class a row matched (e.g. both
    `unit_mismatch` and `confidence_fuzzy`); this returns the
    single canonical value.
    """
    if not candidates:
        return "clean"
    best = "clean"
    best_rank = _DATA_QUALITY_PRECEDENCE["clean"]
    for c in candidates:
        rank = _DATA_QUALITY_PRECEDENCE.get(c)
        if rank is None:
            raise ValueError(
                f"unknown data_quality_flag candidate {c!r}; "
                f"valid: {sorted(DATA_QUALITY_FLAG_VALUES)}"
            )
        if rank > best_rank:
            best, best_rank = c, rank
    return best
