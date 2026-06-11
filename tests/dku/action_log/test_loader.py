from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from vector_flow_connect.dku.action_log import (
    ActionLogSchemaError,
    load_action_log,
)

FIXTURE = (
    Path(__file__).parent.parent.parent / "fixtures" / "action_log" / "synthetic_action_log.csv"
)


def test_load_synthetic_fixture_round_trip() -> None:
    events = load_action_log(FIXTURE)
    assert len(events) == 9
    by_id = {e.event_id: e for e in events}

    deposit = by_id["E0000000001"]
    assert deposit.action == "DEPOSIT"
    assert deposit.event_date == date(2024, 1, 2)
    assert deposit.amount == Decimal("100000.00")
    assert deposit.pool == "留本"
    assert deposit.quantity is None and deposit.nav is None
    assert deposit.note is None  # blank → None

    buy = by_id["E0000000002"]
    assert buy.quantity == Decimal("10000.0000")
    assert buy.nav == Decimal("1.25")
    assert buy.pool is None

    sell = by_id["E0000000006"]
    assert sell.quantity == Decimal("-4000.0000")
    assert sell.note is not None and "realized_return(200.00)" in sell.note

    drip = by_id["E0000000005"]
    assert drip.action == "DRIP"
    assert drip.quantity == Decimal("2828.1100")
    assert drip.nav is None and drip.amount is None
    # quoted note with embedded comma survives csv parsing
    assert drip.note is not None and "units=2,828.11" in drip.note

    perf_fee = by_id["E0000000009"]
    assert perf_fee.action == "PERF_FEE"
    assert perf_fee.share_class == "C"


def test_file_order_preserved() -> None:
    events = load_action_log(FIXTURE)
    assert [e.event_id for e in events][:3] == [
        "E0000000001",
        "E0000000002",
        "E0000000003",
    ]


def test_utf8_sig_and_crlf_tolerated(tmp_path: Path) -> None:
    target = tmp_path / "log.csv"
    content = FIXTURE.read_text(encoding="utf-8").replace("\n", "\r\n")
    target.write_bytes(b"\xef\xbb\xbf" + content.encode("utf-8"))
    events = load_action_log(target)
    assert len(events) == 9


def test_header_drift_raises(tmp_path: Path) -> None:
    target = tmp_path / "log.csv"
    lines = FIXTURE.read_text(encoding="utf-8").splitlines()
    lines[0] = lines[0] + ",surprise_column"
    target.write_text("\n".join(lines), encoding="utf-8")
    with pytest.raises(ActionLogSchemaError, match="surprise_column"):
        load_action_log(target)


def test_needs_dku_confirm_column_tolerated_and_dropped(tmp_path: Path) -> None:
    # dkup's review column ships on the real artifact (2026-06-11b);
    # sanctioned but not contract — values dropped on load.
    target = tmp_path / "log.csv"
    lines = FIXTURE.read_text(encoding="utf-8").splitlines()
    lines[0] = lines[0] + ",needs_dku_confirm"
    lines[1] = lines[1] + ",Y"
    target.write_text("\n".join(lines[:2] + [line + "," for line in lines[2:]]), encoding="utf-8")
    events = load_action_log(target)
    assert len(events) == 9
    assert not hasattr(events[0], "needs_dku_confirm")


def test_unknown_action_raises(tmp_path: Path) -> None:
    target = tmp_path / "log.csv"
    lines = FIXTURE.read_text(encoding="utf-8").splitlines()
    lines[1] = lines[1].replace("DEPOSIT", "TRANSFER")
    target.write_text("\n".join(lines), encoding="utf-8")
    with pytest.raises(ActionLogSchemaError, match="E0000000001"):
        load_action_log(target)


def test_non_numeric_amount_raises(tmp_path: Path) -> None:
    target = tmp_path / "log.csv"
    lines = FIXTURE.read_text(encoding="utf-8").splitlines()
    lines[1] = lines[1].replace("100000.00", "1Ø0")
    target.write_text("\n".join(lines), encoding="utf-8")
    with pytest.raises(ActionLogSchemaError, match="amount"):
        load_action_log(target)


def test_bad_event_id_pattern_raises(tmp_path: Path) -> None:
    target = tmp_path / "log.csv"
    lines = FIXTURE.read_text(encoding="utf-8").splitlines()
    lines[1] = lines[1].replace("E0000000001", "EVT-1")
    target.write_text("\n".join(lines), encoding="utf-8")
    with pytest.raises(ActionLogSchemaError, match="model validation"):
        load_action_log(target)
