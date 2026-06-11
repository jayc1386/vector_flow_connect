"""Canonical helpers for the manager-reports PDF extractor.

Re-exports the source-agnostic ID/dataclass helpers from
`vector_flow_connect.extraction_contract`, which OWNS the shared
dkup-canonical column lists + deterministic ID hashers used by both
this extractor and the `dku.master_record` workbook extractor
(hoisted there in v0.13.0 so this top-level module doesn't depend on
a client subpackage).

Defines its own `SCHEMA_VERSION` / `SOURCE_ID` — a separate version
axis from master_record so each source's re-extracts don't churn the
other's provenance. The `dku_pdf*` values are the DKU-deployment
defaults, retained verbatim for DB provenance continuity (prism's
`lookups.sources` row keys on them); parameterizing them away is
deferred until a second consumer exists.
"""

from __future__ import annotations

from vector_flow_connect.extraction_contract import (  # noqa: F401
    EVENT_COLUMNS,
    FUND_COLUMNS,
    LOT_COLUMNS,
    POSITION_COLUMNS,
    SourceContext,
    empty_event,
    empty_position,
    event_id,
    file_sha256,
    fund_id_stub,
    lot_id,
)

SCHEMA_VERSION = "pdf-0.4.1"
SOURCE_ID = "dku_pdf_v0.4.1"
EXTRACTOR_NAME = "dku_pdf"
EXTRACTOR_VERSION = "0.4.1"

# Kept for backward compat with any external scripts still importing the
# old name. New code should use SCHEMA_VERSION.
PDF_EXTRACTOR_VERSION = SCHEMA_VERSION

OBSERVATION_COLUMNS: list[str] = [
    "observation_type",
    "fund_id",
    "fund_code",
    "source_fund_string",
    "as_of",
    "value",
    "key",
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

AUDIT_DISCREPANCY_COLUMNS: list[str] = [
    "source_artifact",
    "source_artifact_hash",
    "field_path",
    "expected_value",
    "nearest_pdf_value",
    "nearest_diff",
    "source_id",
    "extractor_name",
    "extractor_version",
    "schema_version",
    "extracted_at",
]
