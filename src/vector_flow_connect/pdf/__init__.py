"""PDF extractor subpackage — lifted from
`~/projects/code/dku_prototyping/src/dku/extraction/pdf/` by plan 0038
per the binding prism-vfc split-rule.

Public entry point: `extract_dir(input_dir, out_dir, …)` runs the
inventory → render → LLM (cached) → mapping → audit → write pipeline
over a directory of canonicalized PDFs, producing
`observations.parquet`, `events.parquet`,
`audit_discrepancies.parquet`, plus `extraction_manifest.json` and
`issues.json`.

The extractor is tenant-agnostic. The `dku_pdf` source-id slug is
the *extractor's* name (per the canonical-provenance contract
v1.0.0), not a tenant marker — any tenant routing its manager-monthly
PDFs through this extractor produces rows tagged
`source_id='dku_pdf_v0.4.1'`. The leading `dku_` in the name reflects
the extractor's prototype origin (dkup, the DKU pilot scratch repo),
not a tenant scope.

Consumers in prism: `src/prism/adapters/dku_pdf.py` reads the
`observations.parquet` output and drains `nav_per_unit` rows into
`market_data.prices_raw` via the typed-tool seam.
"""

from __future__ import annotations

from .canonical import (
    AUDIT_DISCREPANCY_COLUMNS,
    EXTRACTOR_NAME,
    EXTRACTOR_VERSION,
    OBSERVATION_COLUMNS,
    PDF_EXTRACTOR_VERSION,
    SCHEMA_VERSION,
    SOURCE_ID,
)
from .runner import extract_dir

__all__ = [
    "AUDIT_DISCREPANCY_COLUMNS",
    "EXTRACTOR_NAME",
    "EXTRACTOR_VERSION",
    "OBSERVATION_COLUMNS",
    "PDF_EXTRACTOR_VERSION",
    "SCHEMA_VERSION",
    "SOURCE_ID",
    "extract_dir",
]
