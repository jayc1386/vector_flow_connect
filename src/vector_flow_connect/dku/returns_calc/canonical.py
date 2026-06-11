"""Canonical schema for the 收益计算 ledger series.

Pure-Python; no I/O.
"""

from __future__ import annotations

from typing import Literal

EXTRACTOR_VERSION = "1"
EXTRACTOR_NAME = "dku_returns_calc"
SCHEMA_VERSION = "dku-returns-calc-v1"
SOURCE_ID = "dku_returns_calc_v1"

# 04 = 留本 (reserved-principal corpus); 05 = 可投 (investable superset).
Scope = Literal["liuben", "ketou"]

# Row taxonomy, ported from dkup `scripts/backfill_action_log.py`
# classification predicates. Informational — downstream gates key off
# the numeric columns (units_delta mints), never off row_kind.
RowKind = Literal[
    "opening",  # 期初余额 seed row
    "external_capital",  # gifts / board authorization (the unit-minting set)
    "income_lumped",  # pre-2022 年/季度收入 (donations+income, unsplittable)
    "dividend",  # 标记=分红 cash dividends
    "interest_realized",  # 已实现结构性存款月度收益
    "unrealized_deposit_interest",  # 未实现结构性存款月度收益
    "unrealized_funds_mtm",  # 浮动收益 marks
    "internal_move",  # 赎回/购买/追投/转出 within the pool
    "other",
]

LEDGER_COLUMNS: list[str] = [
    "scope",
    "as_of",
    "row_kind",
    "mark",  # 标记 (A)
    "debit",  # 借方 (C)
    "realized_flag",  # 已实现 (D): 'Y' | 'N' | None
    "realized_book_balance",  # 账面余额(已实现） (E)
    "active_alloc_delta",  # 主动配置金额变动 (F)
    "active_alloc",  # 主动配置金额 (G)
    "cash_balance",  # 非主动配置金额 (H) — running cash balance
    "total_mv",  # 总资产市值 (I)
    "total_units",  # 总份额 (J)
    "unit_nav",  # 净值 (K) — DKU's unit NAV
    "units_delta",  # ΔJ vs the prior dated row (None on the opening row)
    "content",  # 内容 (L)
    "source_artifact",
    "source_artifact_hash",
    "source_locator",
    "source_id",
    "extractor_name",
    "extractor_version",
    "schema_version",
    "extracted_at",
]
