import json
from pathlib import Path

import pytest

from vector_flow_connect.amac.parse import (
    merge_detail_into_record,
    parse_detail_html,
    parse_pagination,
    parse_search_response,
)
from vector_flow_connect.amac.schema import SCHEMA_VERSION

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def search_payload():
    return json.loads((FIXTURES / "search_response.json").read_text(encoding="utf-8"))


@pytest.fixture
def detail_html():
    return (FIXTURES / "detail_page.html").read_text(encoding="utf-8")


def test_parse_search_response_returns_rows(search_payload):
    rows = parse_search_response(search_payload)
    assert len(rows) > 0
    assert len(rows) == len(search_payload["content"])


def test_parse_search_response_first_row_known_fund(search_payload):
    rows = parse_search_response(search_payload, scraped_at="2026-05-19T11:38:00+00:00")
    first = rows[0]
    assert first["fund_no"] == "S85784"
    assert first["fund_name"] == "宁波同德源余策略壹期创业投资合伙企业（有限合伙）"
    assert first["manager_name"] == "安徽源余私募基金管理有限公司"
    assert first["working_state"] == "正在运作"
    assert first["scraped_at"] == "2026-05-19T11:38:00+00:00"
    assert first["schema_version"] == SCHEMA_VERSION


def test_parse_search_response_dates_are_iso(search_payload):
    rows = parse_search_response(search_payload)
    first = rows[0]
    # putOnRecordDate=1779062400000 ms = 2026-05-13 UTC (rounded)
    assert first["put_on_record_date"] is not None
    assert len(first["put_on_record_date"]) == 10  # "YYYY-MM-DD"
    assert first["establish_date"] is not None


def test_parse_search_response_detail_url_absolute(search_payload):
    rows = parse_search_response(search_payload)
    first = rows[0]
    assert first["detail_url"].startswith("http")
    assert first["detail_url"].endswith(".html")


def test_parse_search_response_handles_empty_content():
    rows = parse_search_response({"content": []})
    assert rows == []


def test_parse_search_response_handles_missing_fields():
    rows = parse_search_response({"content": [{"fundNo": "S00001", "fundName": "test"}]})
    assert len(rows) == 1
    r = rows[0]
    assert r["fund_no"] == "S00001"
    assert r["manager_type"] is None
    assert r["put_on_record_date"] is None
    assert r["managers_info_json"] is None


def test_parse_pagination(search_payload):
    pg = parse_pagination(search_payload)
    assert pg["total_elements"] > 0
    assert pg["page"] == 0
    assert pg["size"] == 20


def test_parse_detail_html_extracts_known_labels(detail_html):
    detail = parse_detail_html(detail_html)
    assert detail["fund_name"] == "宁波同德源余策略壹期创业投资合伙企业（有限合伙）"
    assert detail["fund_no"] == "S85784"
    assert detail["currency"] == "人民币现钞"
    assert detail["working_state"] == "正在运作"
    assert detail["custodian"] == "上海浦东发展银行股份有限公司"
    assert detail["registration_location"] == "浙江省宁波市北仑区"


def test_parse_detail_html_handles_empty_string():
    assert parse_detail_html("") == {}


def test_parse_detail_html_skips_unknown_labels():
    html = "<table><tr><td>不认识的字段:</td><td>some value</td></tr></table>"
    assert parse_detail_html(html) == {}


def test_merge_detail_into_record_preserves_api_fields():
    record = {"fund_no": "S00001", "fund_name": "from API", "manager_name": ""}
    detail = {"fund_name": "from HTML", "manager_name": "from HTML", "currency": "RMB"}
    merged = merge_detail_into_record(record, detail)
    assert merged["fund_name"] == "from API"  # API wins on overlap
    assert merged["manager_name"] == "from HTML"  # empty API field gets filled
    assert merged["currency"] == "RMB"  # detail-only field
