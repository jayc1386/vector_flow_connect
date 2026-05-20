from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from vector_flow_connect.amac.merge import merge_batches
from vector_flow_connect.amac.schema import COLUMN_ORDER, PARQUET_SCHEMA, SCHEMA_VERSION


def _write_batch(path: Path, rows: list[dict]):
    df = pd.DataFrame(rows)
    for col in COLUMN_ORDER:
        if col not in df.columns:
            df[col] = None
    df = df[list(COLUMN_ORDER)]
    pq.write_table(
        pa.Table.from_pandas(df, schema=PARQUET_SCHEMA, preserve_index=False),
        path,
    )


def test_merge_empty_dir_writes_nothing(tmp_path: Path):
    batches = tmp_path / "batches"
    batches.mkdir()
    n = merge_batches(batches, tmp_path / "index.parquet")
    assert n == 0
    assert not (tmp_path / "index.parquet").exists()


def test_merge_single_batch_passthrough(tmp_path: Path):
    batches = tmp_path / "batches"
    batches.mkdir()
    _write_batch(
        batches / "batch_00000.parquet",
        [
            {
                "fund_no": "S00001",
                "fund_name": "A",
                "scraped_at": "2026-05-19T10:00:00+00:00",
                "schema_version": SCHEMA_VERSION,
            },
            {
                "fund_no": "S00002",
                "fund_name": "B",
                "scraped_at": "2026-05-19T10:00:00+00:00",
                "schema_version": SCHEMA_VERSION,
            },
        ],
    )
    n = merge_batches(batches, tmp_path / "index.parquet")
    assert n == 2
    out = pq.read_table(tmp_path / "index.parquet").to_pandas()
    assert sorted(out["fund_no"].tolist()) == ["S00001", "S00002"]


def test_merge_dedups_by_fund_no_keeps_latest(tmp_path: Path):
    batches = tmp_path / "batches"
    batches.mkdir()
    _write_batch(
        batches / "batch_00000.parquet",
        [
            {
                "fund_no": "S00001",
                "fund_name": "A-old",
                "working_state": "正在运作",
                "scraped_at": "2026-05-19T09:00:00+00:00",
                "schema_version": SCHEMA_VERSION,
            },
        ],
    )
    _write_batch(
        batches / "batch_00001.parquet",
        [
            {
                "fund_no": "S00001",
                "fund_name": "A-new",
                "working_state": "已清算",
                "scraped_at": "2026-05-19T11:00:00+00:00",
                "schema_version": SCHEMA_VERSION,
            },
            {
                "fund_no": "S00002",
                "fund_name": "B",
                "working_state": "正在运作",
                "scraped_at": "2026-05-19T11:00:00+00:00",
                "schema_version": SCHEMA_VERSION,
            },
        ],
    )
    n = merge_batches(batches, tmp_path / "index.parquet")
    assert n == 2
    out = pq.read_table(tmp_path / "index.parquet").to_pandas()
    s1 = out[out["fund_no"] == "S00001"].iloc[0]
    assert s1["fund_name"] == "A-new"  # latest scraped_at wins
    assert s1["working_state"] == "已清算"


def test_merge_preserves_parquet_schema(tmp_path: Path):
    batches = tmp_path / "batches"
    batches.mkdir()
    _write_batch(
        batches / "batch_00000.parquet",
        [
            {
                "fund_no": "S00001",
                "fund_name": "A",
                "scraped_at": "2026-05-19T10:00:00+00:00",
                "schema_version": SCHEMA_VERSION,
            },
        ],
    )
    merge_batches(batches, tmp_path / "index.parquet")
    t = pq.read_table(tmp_path / "index.parquet")
    assert t.schema.equals(PARQUET_SCHEMA)


def test_merge_includes_incr_batches(tmp_path: Path):
    batches = tmp_path / "batches"
    batches.mkdir()
    _write_batch(
        batches / "batch_00000.parquet",
        [
            {
                "fund_no": "S00001",
                "fund_name": "A",
                "scraped_at": "2026-05-19T09:00:00+00:00",
                "schema_version": SCHEMA_VERSION,
            },
        ],
    )
    _write_batch(
        batches / "incr_20260520T100000.parquet",
        [
            {
                "fund_no": "S00002",
                "fund_name": "B",
                "scraped_at": "2026-05-20T10:00:00+00:00",
                "schema_version": SCHEMA_VERSION,
            },
        ],
    )
    n = merge_batches(batches, tmp_path / "index.parquet")
    assert n == 2


def test_merge_is_idempotent(tmp_path: Path):
    batches = tmp_path / "batches"
    batches.mkdir()
    _write_batch(
        batches / "batch_00000.parquet",
        [
            {
                "fund_no": "S00001",
                "fund_name": "A",
                "scraped_at": "2026-05-19T10:00:00+00:00",
                "schema_version": SCHEMA_VERSION,
            },
            {
                "fund_no": "S00002",
                "fund_name": "B",
                "scraped_at": "2026-05-19T10:00:00+00:00",
                "schema_version": SCHEMA_VERSION,
            },
        ],
    )
    merge_batches(batches, tmp_path / "index.parquet")
    first_bytes = (tmp_path / "index.parquet").read_bytes()

    merge_batches(batches, tmp_path / "index.parquet")
    second_bytes = (tmp_path / "index.parquet").read_bytes()

    assert first_bytes == second_bytes
