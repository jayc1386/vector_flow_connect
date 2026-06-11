"""Canonical row model + constants for DKU's action log (交易流水台账).

Pure-Python; no I/O. The contract is `ACTION_LOG_SPEC.md` at the dkup
repo root (R1-R8 posting rules, R-V1-R-V5 ingest validations); this
module models the CSV schema verbatim. `action_log` is the English
shorthand used in code — user-facing provenance strings keep the
source-true Chinese name where one exists.

Row-shape notes baked into the model:

- ``event_id`` is a content hash — ``"E" + sha1(date|fund_code|action|
  amount|source_ref|occurrence)[:10]`` — stable across backfill
  regenerations. It names the FACT, not the row: a value correction
  changes the id.
- ``quantity`` is signed (SELL negative; R3 reversing entries negate
  the original's sign).
- ``amount`` is positive by spec; direction comes from ``action``.
  Negative amounts appear only on reversing rows.
- ``pool`` (资金池) is set on DEPOSIT/WITHDRAW and on internal events
  funded by a non-default pool (2026-06-11b: the three 专户 BUYs carry
  pool=非留本); blank means "generalized". 可投资产 = 留本 + 非留本.
- The dkup-side review column ``needs_dku_confirm`` is NOT part of the
  contract and is deliberately not modeled (the loader drops it when
  present).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

EXTRACTOR_NAME = "action_log"
EXTRACTOR_VERSION = "1"
SCHEMA_VERSION = "dku-action-log-v1"
SOURCE_ID = "dku_action_log_v1"

EVENT_ID_PATTERN = r"^E[0-9a-f]{10}$"

Action = Literal[
    "DEPOSIT",  # 注资 — external capital in → cash up
    "WITHDRAW",  # 支取 — external capital out → cash down
    "BUY",  # 申购 — cash → fund units; creates/adds a lot
    "SELL",  # 赎回 — fund units → cash; partials allowed (R6)
    "DIVIDEND",  # 现金分红 — cash distribution received
    "INTEREST",  # 利息收益 — cash-sleeve income
    "DRIP",  # 红利再投 — distribution reinvested; units up, cash-neutral
    "FEE",  # 费用
    "PERF_FEE",  # 业绩报酬 — may be a unit haircut rather than cash
]

# 资金池 vocabulary is PROVISIONAL (spec 2026-06-11): DKU's real
# partition is >=3 buckets (留本 / 专户 / pure cash-management) and the
# naming ruling is pending — free text by design, never an enum.
Pool = str

CASH_FUND_CODE = "CASH"

# CSV column order, verbatim from the spec / fixture header.
COLUMNS: list[str] = [
    "event_id",
    "event_date",
    "fund_code",
    "fund_name",
    "share_class",
    "action",
    "quantity",
    "nav",
    "amount",
    "currency",
    "pool",
    "source_ref",
    "note",
]


class ActionLogEvent(BaseModel):
    """One posted action-log row (R1: per-event, never cumulative).

    The single sanctioned exception to R1 is the cumulative DRIP shape
    (quantity present, nav/amount blank) — first-class per the relay
    decision 2026-06-10T14:42Z; `validate_events` tags it info-level.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str = Field(pattern=EVENT_ID_PATTERN)
    event_date: date
    fund_code: str
    fund_name: str | None = None
    share_class: str | None = None
    action: Action
    quantity: Decimal | None = None
    nav: Decimal | None = None
    amount: Decimal | None = None
    currency: str = "CNY"
    pool: Pool | None = None
    source_ref: str
    note: str | None = None


class RowFinding(BaseModel):
    """A row-level validation finding (maps to prism data-quality findings)."""

    model_config = ConfigDict(frozen=True)

    event_id: str | None
    code: str
    severity: Literal["info", "warning", "error"]
    message: str
    payload: dict[str, Any] = Field(default_factory=dict)
