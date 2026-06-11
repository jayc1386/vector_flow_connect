"""share_class_expectations port (v0.13.0) — loader + flag partition.

The expectations CSV is client-owned reference data passed in by path;
these tests build throwaway CSVs/parquets, nothing client-real.
"""

from __future__ import annotations

import pandas as pd

from vector_flow_connect.dku.master_record.reconcile import (
    apply_data_quality_flags,
    load_expected_share_class_divergence_funds,
)


def _write_expectations(tmp_path, rows):
    path = tmp_path / "share_class_expectations.csv"
    pd.DataFrame(
        rows,
        columns=["fund_id", "held_share_class", "nav_basis", "expected_divergence", "source"],
    ).to_csv(path, index=False)
    return path


def test_loader_none_path_returns_empty():
    assert load_expected_share_class_divergence_funds(None) == set()


def test_loader_absent_file_returns_empty(tmp_path):
    assert load_expected_share_class_divergence_funds(tmp_path / "nope.csv") == set()


def test_loader_parses_truthy_expected_divergence(tmp_path):
    path = _write_expectations(
        tmp_path,
        [
            ("fnd_aaa", "C", "net", "true", "expected"),
            ("fnd_bbb", "A", "representative", "TRUE", "expected, case-insensitive"),
            ("fnd_ccc", "B", "net", "false", "explicitly not expected"),
            ("fnd_ddd", "B", "net", "", "blank → not expected"),
        ],
    )
    assert load_expected_share_class_divergence_funds(path) == {"fnd_aaa", "fnd_bbb"}


def test_loader_missing_columns_returns_empty(tmp_path):
    path = tmp_path / "wrong_shape.csv"
    pd.DataFrame([{"fund_id": "fnd_aaa"}]).to_csv(path, index=False)
    assert load_expected_share_class_divergence_funds(path) == set()


def test_apply_flags_partitions_share_class_vs_nav_mismatch(tmp_path):
    positions = pd.DataFrame(
        [
            {"lot_id": "lot_1", "fund_id": "fnd_expected", "as_of": "2026-05-29"},
            {"lot_id": "lot_2", "fund_id": "fnd_genuine", "as_of": "2026-05-29"},
            {"lot_id": "lot_3", "fund_id": "fnd_clean", "as_of": "2026-05-29"},
        ]
    )
    positions.to_parquet(tmp_path / "positions.parquet", index=False)

    counts = apply_data_quality_flags(
        tmp_path,
        {"unit_issues": [], "drip_gap_rows": []},
        nav_mismatch_fund_ids={"fnd_genuine"},
        share_class_divergence_fund_ids={"fnd_expected"},
    )

    flagged = pd.read_parquet(tmp_path / "positions.parquet").set_index("lot_id")
    assert flagged.loc["lot_1", "data_quality_flag"] == "share_class_net_vs_gross_nav"
    assert flagged.loc["lot_2", "data_quality_flag"] == "nav_mismatch"
    assert flagged.loc["lot_3", "data_quality_flag"] == "clean"
    assert counts["positions"] == 2


def test_share_class_never_masks_real_issues(tmp_path):
    positions = pd.DataFrame(
        [{"lot_id": "lot_1", "fund_id": "fnd_expected", "as_of": "2026-05-29"}]
    )
    positions.to_parquet(tmp_path / "positions.parquet", index=False)

    apply_data_quality_flags(
        tmp_path,
        {
            "unit_issues": [{"lot_id": "lot_1", "as_of": "2026-05-29"}],
            "drip_gap_rows": [],
        },
        share_class_divergence_fund_ids={"fnd_expected"},
    )

    flagged = pd.read_parquet(tmp_path / "positions.parquet")
    assert flagged.loc[0, "data_quality_flag"] == "unit_mismatch"
