"""Loader + row validations for DKU's action log (交易流水台账).

The contract is dkup's `ACTION_LOG_SPEC.md`; the consumable artifact
is the spec-shaped CSV (prism handoff fixture / DKU-maintained
ledger). Built for prism plan 0059 per the binding prism-vfc
split-rule (file-format extraction → vfc, adapter shell → prism).
"""

from .canonical import (  # noqa: F401
    CASH_FUND_CODE,
    COLUMNS,
    EVENT_ID_PATTERN,
    EXTRACTOR_NAME,
    EXTRACTOR_VERSION,
    SCHEMA_VERSION,
    SOURCE_ID,
    Action,
    ActionLogEvent,
    Pool,
    RowFinding,
)
from .loader import ActionLogSchemaError, load_action_log  # noqa: F401
from .validate import validate_events  # noqa: F401
