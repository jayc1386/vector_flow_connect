"""Canonical-schema helpers inherited from dkup's master_record module.

The PDF extractor and the (future) DKU master_record extractor share a
small set of canonical column lists + deterministic ID hashers so that
events produced from PDFs and from workbooks can dedup against each
other. In dkup, these live in `src/dku/extraction/master_record/canonical.py`
and the PDF module imports them as `from ..master_record.canonical
import …`.

Plan 0038 lifts the PDF extractor to `vector_flow_connect.pdf` without
graduating master_record (that's plan 0039+). To keep this Phase A
lift self-contained, we copy the master_record canonical fields the
PDF module depends on into this private sibling module. When
master_record graduates to vfc later, this module either:
  (a) gets superseded by a shared `vector_flow_connect._dku_canonical`
      module that both subpackages import from, OR
  (b) stays as-is and the master_record adapter pulls from its own
      identical module — whichever costs less at the time.

**This module is private to `vector_flow_connect.pdf` (leading
underscore) and should not be imported by external consumers.** The
public `vector_flow_connect.pdf.canonical` re-exports a curated subset.

Pure-Python; no I/O.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal

EXTRACTOR_VERSION = "1"
EXTRACTOR_NAME = "dku_master_record"
SCHEMA_VERSION = "dku-master-record-v1"
SOURCE_ID = "dku_master_record_v1"

EventType = Literal["subscription", "redemption", "dividend", "perf_fee"]
Confidence = Literal["clean", "fuzzy", "reconcile_fail"]

EVENT_COLUMNS: list[str] = [
    "event_id",
    "event_type",
    "fund_id",
    "fund_code",
    "source_fund_string",
    "lot_id",
    "event_date",
    "units_delta",
    "cash_delta",
    "per_unit_amount",
    "eligible_units",
    "payout_form",  # 'cash' | 'reinvested' — dividend events only
    "currency",
    "fx_source",
    "fx_as_of",
    "fx_rate",
    "confidence",
    "data_quality_flag",
    "notes_raw",
    "source_artifact",
    "source_artifact_hash",
    "source_locator",
    "source_id",
    "extractor_name",
    "extractor_version",
    "schema_version",
    "extracted_at",
    "valid_from",
    "recorded_at",
]

LOT_COLUMNS: list[str] = [
    "lot_id",
    "fund_id",
    "fund_code",
    "subscription_date",
    "subscription_event_id",
    "initial_cost",
    "initial_units",
    "initial_nav",
    "currency",
    "data_quality_flag",
    "source_id",
    "schema_version",
]

FUND_COLUMNS: list[str] = [
    "fund_id",
    "source_fund_string",
    "name_zh",
    "fund_code",
    "asset_class",
    "first_seen_as_of",
    "last_seen_as_of",
    "source_id",
    "schema_version",
]

POSITION_COLUMNS: list[str] = [
    "lot_id",
    "fund_id",
    "fund_code",
    "as_of",
    "units",
    "nav",
    "mv",
    "cost",
    "pnl",
    "ann_return",
    "weight_within_fund",
    "position_weight",
    "asset_class",
    "holding_days",
    "data_quality_flag",
    "notes_raw",
    "source_artifact",
    "source_artifact_hash",
    "source_locator",
    "source_id",
    "extractor_name",
    "extractor_version",
    "schema_version",
    "extracted_at",
]

OBSERVATION_COLUMNS: list[str] = [
    "observation_type",
    "fund_id",
    "fund_code",
    "source_fund_string",
    "as_of",
    "value",
    "notes_raw",
    "source_artifact",
    "source_artifact_hash",
    "source_locator",
    "source_id",
    "extractor_name",
    "extractor_version",
    "schema_version",
    "extracted_at",
]


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _short_hash(*parts: object) -> str:
    """Stable short hex hash from concatenated string-cast parts."""
    payload = "|".join("" if p is None else str(p) for p in parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def event_id(
    source_artifact_hash: str,
    event_type: str,
    dedup_key: str,
) -> str:
    """Deterministic event ID — stable across re-extraction AND across
    snapshots that re-describe the same event.

    `dedup_key` is the IDENTITY of the event (not its location). For a
    given event-type the key is constructed from the natural primary
    key of the event:

      subscription:    lot_id
      redemption:      f"{lot_id}:{event_date}"
      dividend cash:   f"{lot_id}:{event_date}:cash:{rounded_cash}"
      dividend DRIP:   f"{lot_id}:{event_date}:drip:{rounded_units}"
      perf_fee:        f"{lot_id}:units:{rounded_units}"

    The source_locator (per-cell coordinate) is stored separately on the
    event row for provenance; it does *not* enter the hash.
    """
    return "evt_" + _short_hash(source_artifact_hash, event_type, dedup_key)


def lot_id(fund_id: str, subscription_date: date, cost_amount: float) -> str:
    """Deterministic lot ID — stable across re-extraction of any source.

    Cost is rounded to 2 decimal places before hashing so floating-point
    noise on re-read doesn't churn the ID.
    """
    sd = subscription_date.isoformat() if subscription_date is not None else ""
    cost_str = f"{round(float(cost_amount), 2):.2f}" if cost_amount is not None else ""
    return "lot_" + _short_hash(fund_id, sd, cost_str)


def fund_id_stub(source_fund_string: str) -> str:
    """Stub canonical fund ID = normalized source string.

    Real UUID assignment + Wind-code mapping is deferred to the
    consumer (prism's lazy-mint path into `reference.securities`
    in plan 0039+).
    """
    normalized = (source_fund_string or "").strip()
    return "fnd_" + _short_hash(normalized)


@dataclass
class SourceContext:
    """Provenance bundle threaded through every parser call."""

    artifact: str  # filename, not full path
    artifact_hash: str  # sha256 hex of the workbook
    extracted_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def empty_event() -> dict:
    """Return an event dict with every canonical column initialized to None.

    Parser writers fill in the fields they have; columns they don't touch
    stay None, which becomes NaN/NaT in the resulting DataFrame.
    """
    return dict.fromkeys(EVENT_COLUMNS)


def empty_position() -> dict:
    return dict.fromkeys(POSITION_COLUMNS)
