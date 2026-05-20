"""Live 5-page bulk-crawl smoke test against the real AMAC API via BrowserClient.

v2.1: Playwright-driven crawl is deterministic — expects ~500 unique rows
after merge-dedup. The page-0 replay at end-of-walk may add a handful of
overlap rows that merge collapses cleanly.

Gated by AMAC_LIVE=1. CI does not run this by default.

    AMAC_LIVE=1 uv run pytest tests/amac/test_bulk_live.py -v
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from vector_flow_connect.amac.browser_client import BrowserClient
from vector_flow_connect.amac.bulk import crawl_bulk
from vector_flow_connect.amac.merge import merge_batches
from vector_flow_connect.amac.schema import PARQUET_SCHEMA
from vector_flow_connect.amac.state import read_state

pytestmark = pytest.mark.skipif(
    not os.getenv("AMAC_LIVE"),
    reason="Live AMAC bulk smoke test; set AMAC_LIVE=1 to enable",
)

_FUND_NO_RE = re.compile(r"^S[A-Z0-9]+$")


def test_bulk_5_pages_produces_valid_parquet(tmp_path: Path):
    with BrowserClient(sleep_between_requests=1.5) as client:
        result = crawl_bulk(
            client,
            out_dir=tmp_path,
            page_size=100,
            max_pages=5,
            checkpoint_every=2,
            sleep=0.0,
            sort="putOnRecordDate,desc",
            resume=False,
            # replay_page_zero=True (default): adds ~100 overlap rows
        )

    assert result.pages_visited == 5
    # 5 pages × 100 + 1 page-0 replay × 100 = 600 rows fetched pre-dedup
    assert result.rows_added == 600

    state = read_state(tmp_path / "crawl_state.json")
    assert state is not None
    assert state.last_page_completed == 4
    assert state.total_elements_at_start is not None
    assert state.total_elements_at_start > 100_000

    batches = sorted((tmp_path / "batches").glob("batch_*.parquet"))
    assert len(batches) >= 1

    for path in batches:
        t = pq.read_table(path)
        assert t.schema.equals(PARQUET_SCHEMA)
        df = t.to_pandas()
        assert df["fund_no"].notna().all()
        assert df["fund_no"].apply(lambda s: bool(_FUND_NO_RE.match(s))).all()

    # BrowserClient pagination is deterministic, so the only expected overlap
    # is from the page-0 replay catching mid-scrape inserts. Expect ~500
    # unique rows; allow a small band for legitimate mid-scrape insertions.
    n = merge_batches(tmp_path / "batches", tmp_path / "index.parquet")
    assert 490 <= n <= 510, (
        f"merge produced {n} unique rows — outside expected 490-510 band; "
        "BrowserClient determinism may have regressed"
    )
    index_t = pq.read_table(tmp_path / "index.parquet")
    assert index_t.schema.equals(PARQUET_SCHEMA)
