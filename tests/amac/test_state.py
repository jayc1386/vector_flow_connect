from vector_flow_connect.amac.state import CrawlState, read_state, write_state


def test_default_state_is_well_formed():
    s = CrawlState()
    assert s.mode == "bulk"
    assert s.last_page_completed == -1
    assert s.rows_collected == 0
    assert s.finished_at is None
    # Empty containers (not shared references)
    assert s.errors == []
    assert s.batches_written == []


def test_roundtrip(tmp_path):
    path = tmp_path / "crawl_state.json"
    original = CrawlState(
        mode="incr",
        last_page_completed=42,
        rows_collected=4200,
        batches_written=["batch_00000.parquet", "batch_00001.parquet"],
        errors=[{"page": 7, "exception": "TimeoutError", "ts": "2026-05-19"}],
    )
    write_state(path, original)
    restored = read_state(path)
    assert restored is not None
    assert restored.mode == "incr"
    assert restored.last_page_completed == 42
    assert restored.rows_collected == 4200
    assert restored.batches_written == ["batch_00000.parquet", "batch_00001.parquet"]
    assert restored.errors[0]["page"] == 7


def test_read_state_missing_returns_none(tmp_path):
    assert read_state(tmp_path / "does_not_exist.json") is None


def test_read_state_corrupt_returns_none(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("not json {")
    assert read_state(path) is None


def test_read_state_extra_keys_returns_none(tmp_path):
    path = tmp_path / "extra.json"
    path.write_text('{"unknown_field": 1}')
    assert read_state(path) is None


def test_write_creates_parent_dirs(tmp_path):
    nested = tmp_path / "a" / "b" / "c" / "state.json"
    write_state(nested, CrawlState())
    assert nested.exists()


def test_write_no_tmp_file_left_after_success(tmp_path):
    path = tmp_path / "state.json"
    write_state(path, CrawlState())
    tmp = path.with_suffix(path.suffix + ".tmp")
    assert not tmp.exists()
