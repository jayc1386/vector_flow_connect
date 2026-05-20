"""Live determinism gate for BrowserClient.

Validates that issuing `fetch()` inside a warm Playwright session against
the AMAC `/api/pof/fund` endpoint returns deterministic, 0-duplicate
pagination — the property raw httpx lacks. This is the design contract
that v2.1 stakes its bulk crawler on.

Gated by AMAC_LIVE=1. CI does not run this by default.

    AMAC_LIVE=1 uv run pytest tests/amac/test_browser_client_live.py -v
"""

from __future__ import annotations

import os

import pytest

from vector_flow_connect.amac.browser_client import BrowserClient
from vector_flow_connect.amac.parse import parse_search_response

pytestmark = pytest.mark.skipif(
    not os.getenv("AMAC_LIVE"),
    reason="Live BrowserClient determinism test; set AMAC_LIVE=1 to enable",
)


def test_search_returns_spring_envelope():
    with BrowserClient(sleep_between_requests=0.5) as client:
        payload = client.search(page=0, size=20, sort="putOnRecordDate,desc")
    assert isinstance(payload, dict)
    assert "content" in payload
    assert isinstance(payload["content"], list)
    assert len(payload["content"]) == 20
    assert payload.get("totalElements", 0) > 100_000
    assert payload.get("size") == 20
    assert payload.get("number") == 0
    assert payload.get("first") is True
    assert payload.get("last") is False


def test_no_dupes_across_10_pages():
    """Walk 10 pages × size=20 = 200 rows; expect 200 unique fund_nos.

    Raw httpx exhibits 15-30% intra-run dupes here (see DEFERRED.md).
    BrowserClient must hit 0% — that's the whole reason for v2.1.
    """
    all_fund_nos: list[str] = []
    with BrowserClient(sleep_between_requests=1.5) as client:
        for page in range(10):
            payload = client.search(page=page, size=20, sort="putOnRecordDate,desc")
            rows = parse_search_response(payload)
            all_fund_nos.extend(r["fund_no"] for r in rows if r.get("fund_no"))

    assert len(all_fund_nos) == 200, f"expected 200 rows across 10 pages, got {len(all_fund_nos)}"
    unique = set(all_fund_nos)
    assert len(unique) == 200, (
        f"DETERMINISM FAILURE: {len(all_fund_nos) - len(unique)} duplicate "
        f"fund_nos across 10 pages — BrowserClient inherits the same drift "
        f"as raw httpx, v2.1 strategy is invalidated"
    )
