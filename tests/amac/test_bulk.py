"""Bulk + incremental crawl unit tests using a mock client. No network."""

from __future__ import annotations

from datetime import timezone
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from vector_flow_connect.amac.bulk import crawl_bulk, crawl_incremental
from vector_flow_connect.amac.schema import PARQUET_SCHEMA
from vector_flow_connect.amac.state import read_state


def _make_envelope(rows: list[dict[str, Any]], *, page: int, size: int, total: int) -> dict:
    total_pages = (total + size - 1) // size
    return {
        "content": rows,
        "number": page,
        "size": size,
        "totalElements": total,
        "totalPages": total_pages,
        "first": page == 0,
        "last": page >= total_pages - 1,
        "numberOfElements": len(rows),
        "sort": None,
    }


def _fund_row(i: int, *, date: str = "2026-05-01") -> dict[str, Any]:
    return {
        "id": f"id-{i:06d}",
        "fundNo": f"S{i:05d}",
        "fundName": f"Test Fund {i}",
        "managerName": "Test Manager",
        "managerType": "受托管理",
        "workingState": "正在运作",
        "putOnRecordDate": _date_to_epoch_ms(date),
        "establishDate": _date_to_epoch_ms(date),
        "isDeputeManage": "否",
        "lastQuarterUpdate": False,
        "url": f"{i:06d}.html",
        "managerUrl": "",
        "mandatorName": "Bank",
        "managersInfo": [],
    }


def _date_to_epoch_ms(iso_date: str) -> int:
    from datetime import datetime

    dt = datetime.fromisoformat(iso_date).replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


class FakeClient:
    """Mock AMACClient that serves canned envelopes by page."""

    def __init__(self, pages: list[list[dict[str, Any]]], *, total: int | None = None):
        self.pages = pages
        self._total = total if total is not None else sum(len(p) for p in pages)
        self.calls: list[dict[str, Any]] = []

    def search(self, *, keyword="", page=0, size=20, sort=None, **extras):
        self.calls.append({"page": page, "size": size, "sort": sort, "extras": extras})
        if page >= len(self.pages):
            return _make_envelope([], page=page, size=size, total=self._total)
        return _make_envelope(self.pages[page], page=page, size=size, total=self._total)


def test_crawl_bulk_walks_all_pages(tmp_path: Path):
    pages = [[_fund_row(i + p * 5) for i in range(5)] for p in range(3)]
    client = FakeClient(pages, total=15)

    result = crawl_bulk(
        client,
        out_dir=tmp_path,
        page_size=5,
        checkpoint_every=2,
        resume=False,
        replay_page_zero=False,
    )

    assert result.rows_added == 15
    assert result.pages_visited == 3
    state = read_state(tmp_path / "crawl_state.json")
    assert state is not None
    assert state.last_page_completed == 2
    assert state.finished_at is not None
    # 3 pages with checkpoint_every=2 → one batch at the 2-page boundary,
    # plus a trailing flush for the final page.
    assert len(state.batches_written) >= 1


def test_crawl_bulk_appends_past_existing_batches(tmp_path: Path):
    """A second --no-resume bulk run must NOT overwrite prior batch files.

    Convergence depends on batches/ being append-only; merge_batches dedups
    across all batches at consolidation time.
    """
    # First pass
    pages = [[_fund_row(i + p * 5) for i in range(5)] for p in range(2)]
    client_a = FakeClient(pages, total=10)
    crawl_bulk(
        client_a,
        out_dir=tmp_path,
        page_size=5,
        checkpoint_every=1,
        resume=False,
        replay_page_zero=False,
    )
    first_batches = sorted((tmp_path / "batches").glob("batch_*.parquet"))
    assert len(first_batches) >= 1
    first_names = [p.name for p in first_batches]

    # Second pass: same client, --no-resume (fresh state). Bulk must
    # bump batch_index past the existing files instead of overwriting.
    pages2 = [[_fund_row(i + p * 5 + 100) for i in range(5)] for p in range(2)]
    client_b = FakeClient(pages2, total=10)
    crawl_bulk(
        client_b,
        out_dir=tmp_path,
        page_size=5,
        checkpoint_every=1,
        resume=False,
        replay_page_zero=False,
    )
    all_batches = sorted((tmp_path / "batches").glob("batch_*.parquet"))
    all_names = [p.name for p in all_batches]

    # All of pass 1's files must still exist
    for name in first_names:
        assert name in all_names, f"pass 2 overwrote {name} — convergence broken"
    # Pass 2 added new files
    assert len(all_batches) > len(first_batches)


def test_crawl_bulk_replays_page_zero(tmp_path: Path):
    """With replay_page_zero=True (default), page 0 is fetched a second
    time at end-of-walk to catch mid-scrape inserts. Merge-dedup handles
    the resulting overlap; here we just assert the extra call happened."""
    pages = [[_fund_row(i + p * 5) for i in range(5)] for p in range(3)]
    client = FakeClient(pages, total=15)

    crawl_bulk(
        client,
        out_dir=tmp_path,
        page_size=5,
        checkpoint_every=10,  # one big batch to keep things simple
        resume=False,
        # replay_page_zero=True is the default
    )

    pages_called = [c["page"] for c in client.calls]
    assert pages_called == [0, 1, 2, 0], (
        f"expected forward walk 0,1,2 then page-0 replay; got {pages_called}"
    )


def test_crawl_bulk_resumes_from_state(tmp_path: Path):
    pages = [[_fund_row(i + p * 5) for i in range(5)] for p in range(4)]
    client_a = FakeClient(pages, total=20)
    crawl_bulk(
        client_a,
        out_dir=tmp_path,
        page_size=5,
        max_pages=2,
        checkpoint_every=1,
        resume=False,
        replay_page_zero=False,
    )

    state_after_first = read_state(tmp_path / "crawl_state.json")
    assert state_after_first is not None
    assert state_after_first.last_page_completed == 1

    # Second invocation: should resume at page 2
    client_b = FakeClient(pages, total=20)
    result = crawl_bulk(
        client_b, out_dir=tmp_path, page_size=5, checkpoint_every=1, replay_page_zero=False
    )
    pages_visited = [c["page"] for c in client_b.calls]
    assert pages_visited[0] == 2
    assert result.pages_visited == 2  # only pages 2 and 3


def test_crawl_bulk_writes_valid_parquet(tmp_path: Path):
    pages = [[_fund_row(i) for i in range(3)]]
    client = FakeClient(pages, total=3)

    crawl_bulk(
        client,
        out_dir=tmp_path,
        page_size=3,
        checkpoint_every=1,
        resume=False,
        replay_page_zero=False,
    )
    batches = list((tmp_path / "batches").glob("batch_*.parquet"))
    assert len(batches) >= 1
    t = pq.read_table(batches[0])
    assert t.schema.equals(PARQUET_SCHEMA)
    assert t.num_rows == 3
    df = t.to_pandas()
    assert (df["fund_no"].str.startswith("S")).all()


def test_crawl_bulk_honors_max_pages(tmp_path: Path):
    pages = [[_fund_row(i + p * 5) for i in range(5)] for p in range(10)]
    client = FakeClient(pages, total=50)

    result = crawl_bulk(
        client,
        out_dir=tmp_path,
        page_size=5,
        max_pages=3,
        checkpoint_every=10,
        resume=False,
        replay_page_zero=False,
    )
    assert result.pages_visited == 3
    assert result.rows_added == 15


def test_crawl_bulk_retries_on_exception(tmp_path: Path):
    pages = [[_fund_row(i) for i in range(2)]]
    client = FakeClient(pages, total=2)

    # Wrap search to fail once then succeed
    real_search = client.search
    state = {"fails_remaining": 2}

    def flaky_search(**kw):
        if state["fails_remaining"] > 0:
            state["fails_remaining"] -= 1
            raise ConnectionError("simulated")
        return real_search(**kw)

    client.search = flaky_search  # type: ignore[assignment]

    # Patch time.sleep to skip the backoffs
    from vector_flow_connect.amac import bulk

    orig_sleep = bulk.time.sleep
    bulk.time.sleep = lambda *_: None  # type: ignore[assignment]
    try:
        result = crawl_bulk(
            client,
            out_dir=tmp_path,
            page_size=2,
            checkpoint_every=1,
            resume=False,
            replay_page_zero=False,
        )
    finally:
        bulk.time.sleep = orig_sleep  # type: ignore[assignment]

    assert result.rows_added == 2
    assert state["fails_remaining"] == 0


def test_crawl_incremental_stops_after_known_plus_safety(tmp_path: Path):
    # 4 pages, newest-first. The local index already has fund_no S00003
    # (which will appear on page 1). With safety_pages=1, the crawler
    # walks pages 0,1 (finds known on 1), then 1 safety page (2), then stops.
    # Page 3 is never fetched. The kept rows are S00000-S00002 + S00004,
    # S00005 (page 2 unseen rows). S00003 is dropped because it's already in
    # the local index.
    pages = [
        [_fund_row(0), _fund_row(1)],
        [_fund_row(2), _fund_row(3)],  # S00003 is known
        [_fund_row(4), _fund_row(5)],  # safety page
        [_fund_row(6), _fund_row(7)],  # MUST NOT be fetched
    ]
    client = FakeClient(pages, total=8)

    result = crawl_incremental(
        client,
        out_dir=tmp_path,
        seen_fund_nos={"S00003"},
        page_size=2,
        safety_pages=1,
    )
    assert result.pages_visited == 3
    # 6 rows fetched (pages 0,1,2 × 2 each); 1 dropped as known → 5 kept
    assert result.rows_added == 5
    pages_called = [c["page"] for c in client.calls]
    assert pages_called == [0, 1, 2], (
        f"crawler should have stopped after safety page; got {pages_called}"
    )
    sorts = {c["sort"] for c in client.calls}
    assert sorts == {"putOnRecordDate,desc"}


def test_crawl_incremental_zero_safety_pages_stops_immediately(tmp_path: Path):
    pages = [
        [_fund_row(0), _fund_row(1)],
        [_fund_row(2), _fund_row(3)],  # S00002 is known
        [_fund_row(4), _fund_row(5)],  # MUST NOT be fetched (safety=0)
    ]
    client = FakeClient(pages, total=6)

    result = crawl_incremental(
        client,
        out_dir=tmp_path,
        seen_fund_nos={"S00002"},
        page_size=2,
        safety_pages=0,
    )
    assert result.pages_visited == 2
    assert result.rows_added == 3  # S00000, S00001, S00003
    pages_called = [c["page"] for c in client.calls]
    assert pages_called == [0, 1]


def test_crawl_incremental_empty_seen_set_walks_to_end(tmp_path: Path):
    """When seen_fund_nos is empty, no row is "known", so the crawler walks
    to the last page (or max_pages cap) without triggering stop-on-known."""
    pages = [
        [_fund_row(0), _fund_row(1)],
        [_fund_row(2), _fund_row(3)],
    ]
    client = FakeClient(pages, total=4)

    result = crawl_incremental(
        client,
        out_dir=tmp_path,
        seen_fund_nos=set(),
        page_size=2,
    )
    assert result.pages_visited == 2
    assert result.rows_added == 4
