"""AMAC fund record schema.

Field list derived from the 2026-05-19 recon (see DISCOVERY.md). Combines the
JSON list API (14 fields) with the detail-page HTML (19 label/value pairs).
Overlapping fields are unified under one snake_case name.

See `data/raw/amac_recon/detail_fields.json` for the live field inventory and
`data/raw/amac_recon/search_response.json` for the live API row sample.
"""

from __future__ import annotations

from typing import TypedDict

import pyarrow as pa

SCHEMA_VERSION = "amac-1.0"
SOURCE_ID = "amac_index_v1.0"


class AMACRecord(TypedDict, total=False):
    # --- index API fields (from POST /amac-infodisc/api/pof/fund) ---
    internal_id: str  # AMAC's internal numeric ID
    fund_no: str  # 备案编号 (e.g. "S85784") — THE KEY FIELD
    fund_name: str  # 基金名称
    manager_name: str  # 基金管理人名称
    manager_type: str | None  # 管理类型 (受托管理 etc.)
    working_state: str | None  # 运作状态 (正在运作 / 已清算 / ...)
    put_on_record_date: str | None  # 备案时间 (ISO date)
    establish_date: str | None  # 成立时间 (ISO date)
    is_depute_manage: str | None  # 是/否
    last_quarter_update: bool | None
    detail_url: str | None  # absolute URL to the HTML detail page
    manager_url: str | None  # absolute URL to manager profile
    mandator_name: str | None  # entrustor (often == custodian; capture both)
    managers_info_json: str | None  # JSON-encoded list of {managerId, managerName, managerUrl}

    # --- detail-page-only fields (from /amac-infodisc/res/pof/fund/{id}.html) ---
    filing_stage: str | None  # 基金备案阶段
    fund_type: str | None  # 基金类型
    registration_location: str | None  # 注册地
    currency: str | None  # 币种
    custodian: str | None  # 托管人名称
    last_updated: str | None  # 基金信息最后更新时间
    monthly_report_status: str | None  # 月报: "应披露N条, 按时披露N条, 未披露N条"
    quarterly_report_status: str | None  # 季报
    semiannual_report_status: str | None  # 半年报
    annual_report_status: str | None  # 年报
    investor_query_account_rate: str | None  # 投资者查询账号开立率 (e.g. "0.00%")
    institution_alert: str | None  # 机构提示信息

    # --- provenance ---
    scraped_at: str  # ISO datetime when this row was captured
    schema_version: str  # SCHEMA_VERSION at scrape time
    source_id: str  # SOURCE_ID slug per prism canonical_provenance v1.0.0


PARQUET_SCHEMA = pa.schema(
    [
        ("internal_id", pa.string()),
        ("fund_no", pa.string()),
        ("fund_name", pa.string()),
        ("manager_name", pa.string()),
        ("manager_type", pa.string()),
        ("working_state", pa.string()),
        ("put_on_record_date", pa.string()),
        ("establish_date", pa.string()),
        ("is_depute_manage", pa.string()),
        ("last_quarter_update", pa.bool_()),
        ("detail_url", pa.string()),
        ("manager_url", pa.string()),
        ("mandator_name", pa.string()),
        ("managers_info_json", pa.string()),
        ("filing_stage", pa.string()),
        ("fund_type", pa.string()),
        ("registration_location", pa.string()),
        ("currency", pa.string()),
        ("custodian", pa.string()),
        ("last_updated", pa.string()),
        ("monthly_report_status", pa.string()),
        ("quarterly_report_status", pa.string()),
        ("semiannual_report_status", pa.string()),
        ("annual_report_status", pa.string()),
        ("investor_query_account_rate", pa.string()),
        ("institution_alert", pa.string()),
        ("scraped_at", pa.string()),
        ("schema_version", pa.string()),
        ("source_id", pa.string()),
    ]
)


COLUMN_ORDER: tuple[str, ...] = tuple(f.name for f in PARQUET_SCHEMA)
