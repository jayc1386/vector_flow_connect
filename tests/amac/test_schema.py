import pyarrow as pa

from vector_flow_connect.amac.schema import COLUMN_ORDER, PARQUET_SCHEMA, SCHEMA_VERSION, AMACRecord


def test_schema_columns_match_typeddict_keys():
    typed_keys = set(AMACRecord.__annotations__.keys())
    schema_keys = set(COLUMN_ORDER)
    assert typed_keys == schema_keys, (
        f"TypedDict and parquet schema diverged. "
        f"In TypedDict only: {typed_keys - schema_keys}. "
        f"In schema only: {schema_keys - typed_keys}."
    )


def test_empty_dataframe_roundtrips_through_parquet_schema(tmp_path):
    arrays = [pa.array([], type=field.type) for field in PARQUET_SCHEMA]
    table = pa.Table.from_arrays(arrays, schema=PARQUET_SCHEMA)
    path = tmp_path / "empty.parquet"
    import pyarrow.parquet as pq

    pq.write_table(table, path)
    reread = pq.read_table(path)
    assert reread.schema.equals(PARQUET_SCHEMA)
    assert reread.num_rows == 0


def test_schema_version_pinned():
    assert SCHEMA_VERSION == "amac-1.0"
