"""Parser for DKU's `留本基金动态资产配置情况.xlsx` master workbook.

`master_record` is the English shorthand used in code for ergonomic
reasons — the on-disk filename and any user-facing provenance string
always uses the source-true Chinese name.

Lifted from `dku.extraction.master_record` by prism plan 0039 per
the binding prism-vfc split-rule (scraper-shape → vfc, adapter-shell
→ prism).
"""

from .canonical import (  # noqa: F401
    EVENT_COLUMNS,
    EXTRACTOR_NAME,
    EXTRACTOR_VERSION,
    FUND_COLUMNS,
    LOT_COLUMNS,
    OBSERVATION_COLUMNS,
    POSITION_COLUMNS,
    SCHEMA_VERSION,
    SOURCE_ID,
    EventType,
    SourceContext,
    empty_event,
    empty_position,
    event_id,
    file_sha256,
    fund_id_stub,
    lot_id,
)
from .workbook import extract  # noqa: F401
