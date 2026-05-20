"""Centralized AMAC endpoint URLs + field label translations.

All magic strings live here so site drift becomes a one-file patch.
"""

from __future__ import annotations

INDEX_BASE = "http://gs.amac.org.cn/amac-infodisc"

# POST endpoint; body is JSON filter object; ?page=N&size=N in query string.
FUND_LIST_ENDPOINT = f"{INDEX_BASE}/api/pof/fund"

# GET endpoint; {id}.html where id matches AMACRecord.internal_id.
FUND_DETAIL_BASE = f"{INDEX_BASE}/res/pof/fund"


def detail_url(internal_id: str) -> str:
    return f"{FUND_DETAIL_BASE}/{internal_id}.html"


# --- Detail-page Chinese-label → snake_case key map ---
# Discovered in recon. Labels appear as `<td>label:</td><td>value</td>` rows.
# Trailing colons (both `:` and `：`) are stripped before lookup.
DETAIL_LABEL_MAP: dict[str, str] = {
    "基金名称": "fund_name",
    "基金编号": "fund_no",
    "成立时间": "establish_date",
    "备案时间": "put_on_record_date",
    "基金备案阶段": "filing_stage",
    "基金类型": "fund_type",
    "注册地": "registration_location",
    "币种": "currency",
    "基金管理人名称": "manager_name",
    "管理类型": "manager_type",
    "托管人名称": "custodian",
    "运作状态": "working_state",
    "基金信息最后更新时间": "last_updated",
    "月报": "monthly_report_status",
    "季报": "quarterly_report_status",
    "半年报": "semiannual_report_status",
    "年报": "annual_report_status",
    "投资者查询账号开立率": "investor_query_account_rate",
    "机构提示信息": "institution_alert",
}
