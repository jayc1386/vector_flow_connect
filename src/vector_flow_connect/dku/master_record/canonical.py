"""Canonical schema + deterministic ID hashing for master-record events.

The shared dkup-canonical column lists + ID hashers were hoisted to
`vector_flow_connect.extraction_contract` in v0.13.0 (they're consumed
by the top-level `manager_reports` extractor too); this module
re-exports them unchanged alongside the master_record-specific
constants, so downstream imports keep working.

Pure-Python; no I/O.
"""

from __future__ import annotations

from typing import Literal

from vector_flow_connect.extraction_contract import (  # noqa: F401
    EVENT_COLUMNS,
    FUND_COLUMNS,
    LOT_COLUMNS,
    POSITION_COLUMNS,
    SourceContext,
    _short_hash,
    empty_event,
    empty_position,
    event_id,
    file_sha256,
    fund_id_stub,
    lot_id,
)

EXTRACTOR_VERSION = "1"
EXTRACTOR_NAME = "dku_master_record"
SCHEMA_VERSION = "dku-master-record-v1"
SOURCE_ID = "dku_master_record_v1"

EventType = Literal["subscription", "redemption", "dividend", "perf_fee"]
Confidence = Literal["clean", "fuzzy", "reconcile_fail"]
CodeConfidence = Literal["confirmed", "tentative"]

OBSERVATION_COLUMNS: list[str] = [
    "observation_type",
    "fund_id",
    "fund_code",
    "code_confidence",
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
