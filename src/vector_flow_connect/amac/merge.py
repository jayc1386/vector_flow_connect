"""Merge per-batch parquet files into a deduplicated index.parquet.

Dedup semantics: for rows sharing the same `fund_no`, keep the one with
the latest `scraped_at`. The result preserves PARQUET_SCHEMA so downstream
readers see a stable shape.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from vector_flow_connect.amac.schema import COLUMN_ORDER, PARQUET_SCHEMA


def merge_batches(batches_dir: Path, index_path: Path) -> int:
    """Merge all `batch_*.parquet` and `incr_*.parquet` files into `index_path`.

    Returns the row count of the final index. Writes nothing if there are
    no batch files.
    """
    batches_dir = Path(batches_dir)
    index_path = Path(index_path)

    paths = sorted(batches_dir.glob("batch_*.parquet")) + sorted(batches_dir.glob("incr_*.parquet"))
    if not paths:
        return 0

    frames = [pq.read_table(p).to_pandas() for p in paths]
    df = pd.concat(frames, ignore_index=True)

    # Dedup: keep the row with the latest scraped_at per fund_no
    df = df.sort_values("scraped_at", ascending=True, kind="stable")
    df = df.drop_duplicates(subset=["fund_no"], keep="last")
    df = df.reset_index(drop=True)

    # Ensure all PARQUET_SCHEMA columns present in declared order
    for col in COLUMN_ORDER:
        if col not in df.columns:
            df[col] = None
    df = df[list(COLUMN_ORDER)]

    index_path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pandas(df, schema=PARQUET_SCHEMA, preserve_index=False)
    pq.write_table(table, index_path)
    return len(df)
