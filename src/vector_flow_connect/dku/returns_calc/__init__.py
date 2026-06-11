"""DKU 收益计算 workbook extractor (returns-calculation ledgers).

Parses the 追溯调整至2022 ledger shared by DKU's two return-calculation
workbooks — `04. 留本基金收益计算.xlsx` (scope ``liuben``) and
`05. 可投资产收益计算.xlsx` (scope ``ketou``) — into a typed
ledger-series parquet. The ledger is DKU's own unitization: dated rows
carrying 总资产市值 / 总份额 / 净值 (their unit NAV) alongside the
running cash balance and per-row income/flow marks.

v1 (prism plan 0062) parses the ledger sheet ONLY. The deposit sheets
(结构性存款台账/明细) served a cash-sleeve valuation recipe that was
descoped when DKU confirmed the 现金产品 pool's 留本/非留本 attribution
is manual; they may join in a later version.
"""

from .canonical import (  # noqa: F401
    EXTRACTOR_NAME,
    EXTRACTOR_VERSION,
    LEDGER_COLUMNS,
    SCHEMA_VERSION,
    SOURCE_ID,
    RowKind,
    Scope,
)
from .ledger import LEDGER_SHEET_NAME, parse_ledger  # noqa: F401
from .workbook import extract  # noqa: F401
