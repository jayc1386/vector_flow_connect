"""Bulk + incremental crawl of AMAC's 私募 index API.

`crawl_bulk` walks all pages forward (default sort `putOnRecordDate,desc`),
checkpointing every N pages by writing a batch parquet under
`<out_dir>/batches/` and updating `<out_dir>/crawl_state.json` atomically.
Resumable: a second invocation continues from `last_page_completed + 1`
if state exists. After the main walk it re-fetches page 0 to catch funds
inserted at the top during the scrape; merge_batches dedupes by fund_no.

`crawl_incremental` walks newest-first and stops after seeing a fund whose
`fund_no` is already in `seen_fund_nos` plus one safety page. Callers
typically pre-populate `seen_fund_nos` from the local `index.parquet`.

Mock-driven tests inject an `AMACClient`-shaped object. v2.1 production
uses `BrowserClient` (src/amac/browser_client.py) for deterministic
pagination; raw `AMACClient` (src/amac/client.py) stays for v1 targeted
lookup where ~6% miss is acceptable.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from vector_flow_connect.amac.parse import parse_pagination, parse_search_response
from vector_flow_connect.amac.schema import COLUMN_ORDER, PARQUET_SCHEMA, AMACRecord
from vector_flow_connect.amac.state import CrawlState, read_state, write_state

log = logging.getLogger(__name__)


# --- Client protocol (so tests can pass a mock) -----------------


class _SearchClient(Protocol):
    def search(
        self,
        *,
        keyword: str = "",
        page: int = 0,
        size: int = 20,
        sort: str | None = None,
        **extra_filters: str,
    ) -> dict: ...


# --- Helpers ----------------------------------------------------


def _rows_to_table(rows: list[dict[str, Any]]) -> pa.Table:
    """Build a pyarrow Table matching PARQUET_SCHEMA, padding missing cols."""
    df = pd.DataFrame(rows)
    for col in COLUMN_ORDER:
        if col not in df.columns:
            df[col] = None
    df = df[list(COLUMN_ORDER)]
    return pa.Table.from_pandas(df, schema=PARQUET_SCHEMA, preserve_index=False)


def _batch_filename(batch_index: int) -> str:
    return f"batch_{batch_index:05d}.parquet"


def _highest_existing_batch_index(batches_dir: Path) -> int:
    """Return the highest NNNNN seen in batches_dir/batch_NNNNN.parquet, or -1.

    Used to bump the batch counter past existing files so a second
    --no-resume bulk pass doesn't overwrite the first pass's batches.
    Multi-pass convergence depends on batches/ being append-only.
    """
    if not batches_dir.exists():
        return -1
    indices: list[int] = []
    for p in batches_dir.glob("batch_*.parquet"):
        try:
            indices.append(int(p.stem.split("_")[-1]))
        except ValueError:
            continue
    return max(indices, default=-1)


def _write_batch(batches_dir: Path, batch_index: int, rows: list[dict[str, Any]]) -> str:
    batches_dir.mkdir(parents=True, exist_ok=True)
    name = _batch_filename(batch_index)
    pq.write_table(_rows_to_table(rows), batches_dir / name)
    return name


def _retrying_search(
    client: _SearchClient,
    *,
    page: int,
    page_size: int,
    sort: str | None,
    filter_body: dict[str, Any],
    max_retries: int = 5,
) -> dict:
    """Call client.search with exponential backoff on transient failures.

    On a non-retryable error (or after max_retries), raise.
    """
    last_exc: BaseException | None = None
    for attempt in range(max_retries):
        try:
            return client.search(page=page, size=page_size, sort=sort, **filter_body)
        except Exception as exc:
            last_exc = exc
            backoff = min(60.0, (2**attempt) + random.random())
            log.warning(
                "search(page=%d) failed (attempt %d/%d): %s; sleeping %.1fs",
                page,
                attempt + 1,
                max_retries,
                exc,
                backoff,
            )
            time.sleep(backoff)
    assert last_exc is not None
    raise last_exc


# --- Public API -------------------------------------------------


@dataclass
class CrawlResult:
    state: CrawlState
    rows_added: int
    pages_visited: int


def crawl_bulk(
    client: _SearchClient,
    *,
    out_dir: Path,
    page_size: int = 100,
    max_pages: int | None = None,
    checkpoint_every: int = 10,
    sleep: float = 0.0,  # client already sleeps; bulk only adds extra pause
    filter_body: dict[str, Any] | None = None,
    sort: str | None = "putOnRecordDate,desc",
    resume: bool = True,
    replay_page_zero: bool = True,
) -> CrawlResult:
    """Full forward-paginated crawl. Resumable via crawl_state.json.

    Default `sort` is `putOnRecordDate,desc` (newest first). Funds inserted
    mid-scrape land at page 0, so `replay_page_zero=True` re-fetches page 0
    after the main walk and appends its rows to the buffer. Dedup-by-fund_no
    at `merge_batches()` (keep latest scraped_at) consolidates the overlap.

    `sleep` adds an extra pause AFTER each request on top of whatever the
    client itself sleeps for. Set to 0 to defer all throttling to the client.
    """
    out_dir = Path(out_dir)
    batches_dir = out_dir / "batches"
    state_path = out_dir / "crawl_state.json"
    filter_body = filter_body or {}

    state = read_state(state_path) if resume else None
    if state is None:
        state = CrawlState(
            mode="bulk",
            page_size=page_size,
            sleep_seconds=sleep,
            filter_body=filter_body,
            sort=sort,
        )

    start_page = state.last_page_completed + 1
    initial_rows_collected = state.rows_collected
    pages_visited = 0
    buffer: list[AMACRecord] = []
    batch_index = max(
        len(state.batches_written),
        _highest_existing_batch_index(batches_dir) + 1,
    )

    page = start_page
    while True:
        if max_pages is not None and pages_visited >= max_pages:
            break

        payload = _retrying_search(
            client,
            page=page,
            page_size=page_size,
            sort=sort,
            filter_body=filter_body,
        )

        if state.total_pages_at_start is None:
            pg = parse_pagination(payload)
            state.total_pages_at_start = pg["total_pages"]
            state.total_elements_at_start = pg["total_elements"]

        rows = parse_search_response(payload)
        if not rows and payload.get("last", True):
            state.last_page_completed = page
            break

        buffer.extend(rows)
        for r in rows:
            d = r.get("put_on_record_date")
            if d and (
                state.max_put_on_record_date_seen is None or d > state.max_put_on_record_date_seen
            ):
                state.max_put_on_record_date_seen = d

        state.last_page_completed = page
        state.rows_collected += len(rows)
        pages_visited += 1

        # Checkpoint
        if len(buffer) >= checkpoint_every * page_size or payload.get("last"):
            if buffer:
                name = _write_batch(batches_dir, batch_index, buffer)
                state.batches_written.append(name)
                batch_index += 1
                buffer = []
            write_state(state_path, state)

        if payload.get("last"):
            break

        page += 1
        if sleep:
            time.sleep(sleep)

    # Page-0 replay: with sort=desc, mid-scrape inserts land at the top.
    # Re-fetch page 0 so merge.merge_batches sees them; dedup keeps latest.
    if replay_page_zero and pages_visited > 0:
        payload = _retrying_search(
            client,
            page=0,
            page_size=page_size,
            sort=sort,
            filter_body=filter_body,
        )
        replay_rows = parse_search_response(payload)
        buffer.extend(replay_rows)
        state.rows_collected += len(replay_rows)

    # Flush any trailing buffer
    if buffer:
        name = _write_batch(batches_dir, batch_index, buffer)
        state.batches_written.append(name)

    state.finished_at = datetime.now(timezone.utc).isoformat()
    write_state(state_path, state)

    return CrawlResult(
        state=state,
        rows_added=state.rows_collected - initial_rows_collected,
        pages_visited=pages_visited,
    )


def crawl_incremental(
    client: _SearchClient,
    *,
    out_dir: Path,
    seen_fund_nos: set[str],
    page_size: int = 100,
    sleep: float = 0.0,
    filter_body: dict[str, Any] | None = None,
    max_pages: int | None = None,
    safety_pages: int = 1,
) -> CrawlResult:
    """Walk most-recent filings first; stop after the first page containing
    a known fund + `safety_pages` extra pages.

    `seen_fund_nos` is the set of fund_no values already present in the local
    index (typically `set(pd.read_parquet(INDEX_PATH, columns=['fund_no'])
    ['fund_no'])`). Rows whose `fund_no` is in this set are dropped from the
    output batch; their presence on a page only signals "the new filings
    above this point have already been captured."

    All unseen rows from every page walked (including pages walked during the
    safety window) are written to a single incremental batch parquet.
    """
    out_dir = Path(out_dir)
    batches_dir = out_dir / "batches"
    state_path = out_dir / "crawl_state.json"
    filter_body = filter_body or {}

    state = CrawlState(
        mode="incr",
        page_size=page_size,
        sleep_seconds=sleep,
        filter_body=filter_body,
        sort="putOnRecordDate,desc",
    )

    buffer: list[AMACRecord] = []
    pages_visited = 0
    extra_pages_after_first_hit = 0
    saw_known = False
    page = 0

    while True:
        if max_pages is not None and pages_visited >= max_pages:
            break

        payload = _retrying_search(
            client,
            page=page,
            page_size=page_size,
            sort="putOnRecordDate,desc",
            filter_body=filter_body,
        )

        if state.total_pages_at_start is None:
            pg = parse_pagination(payload)
            state.total_pages_at_start = pg["total_pages"]
            state.total_elements_at_start = pg["total_elements"]

        rows = parse_search_response(payload)
        if not rows:
            break

        kept = [r for r in rows if r.get("fund_no") not in seen_fund_nos]
        buffer.extend(kept)
        state.rows_collected += len(kept)
        state.last_page_completed = page
        pages_visited += 1

        page_has_known = any(r.get("fund_no") in seen_fund_nos for r in rows)
        if page_has_known:
            saw_known = True

        if saw_known:
            if extra_pages_after_first_hit >= safety_pages:
                break
            extra_pages_after_first_hit += 1

        if payload.get("last"):
            break

        page += 1
        if sleep:
            time.sleep(sleep)

    # Single-batch write for incrementer output
    if buffer:
        name = f"incr_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}.parquet"
        batches_dir.mkdir(parents=True, exist_ok=True)
        pq.write_table(_rows_to_table(buffer), batches_dir / name)
        state.batches_written.append(name)

    state.finished_at = datetime.now(timezone.utc).isoformat()
    write_state(state_path, state)

    return CrawlResult(state=state, rows_added=len(buffer), pages_visited=pages_visited)
