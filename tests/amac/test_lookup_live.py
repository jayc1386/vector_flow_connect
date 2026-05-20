"""Live-network integration test. Gated by AMAC_LIVE=1 env var.

Run locally with:
    AMAC_LIVE=1 uv run pytest tests/amac/test_lookup_live.py -v

CI does NOT run this by default — see DEFERRED.md re: scheduling.
"""

from __future__ import annotations

import os

import pytest

from vector_flow_connect.amac.client import AMACClient
from vector_flow_connect.amac.lookup import search_by_name

pytestmark = pytest.mark.skipif(
    not os.getenv("AMAC_LIVE"),
    reason="Live AMAC test; set AMAC_LIVE=1 to enable",
)


def test_search_returns_matches_for_known_token():
    with AMACClient() as client:
        rows = search_by_name(client, "睿见1号", max_results=10)
    assert len(rows) >= 1
    for r in rows:
        assert r["fund_no"].startswith("S")
        assert r["fund_name"]
        assert r["manager_name"]
        assert "睿见" in r["fund_name"] or "睿见1号" in r["fund_name"]


def test_search_enriches_with_detail_when_requested():
    with AMACClient() as client:
        rows = search_by_name(client, "睿见1号", max_results=2, enrich_with_detail=True)
    assert len(rows) >= 1
    first = rows[0]
    # Detail-only fields should be populated
    assert first.get("filing_stage") or first.get("fund_type") or first.get("currency")
