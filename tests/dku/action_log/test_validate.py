from datetime import date
from decimal import Decimal
from pathlib import Path

from vector_flow_connect.dku.action_log import (
    ActionLogEvent,
    load_action_log,
    validate_events,
)

FIXTURE = (
    Path(__file__).parent.parent.parent / "fixtures" / "action_log" / "synthetic_action_log.csv"
)


def _event(**overrides: object) -> ActionLogEvent:
    base: dict[str, object] = {
        "event_id": "Eaaaaaaaaaa",
        "event_date": date(2024, 1, 2),
        "fund_code": "000001",
        "fund_name": "合成基金甲",
        "share_class": None,
        "action": "BUY",
        "quantity": Decimal("10000"),
        "nav": Decimal("1.25"),
        "amount": Decimal("12500.00"),
        "currency": "CNY",
        "pool": None,
        "source_ref": "synthetic",
        "note": None,
    }
    base.update(overrides)
    return ActionLogEvent(**base)  # type: ignore[arg-type]


def test_synthetic_fixture_yields_only_expected_info_findings() -> None:
    findings = validate_events(load_action_log(FIXTURE))
    assert {f.severity for f in findings} == {"info"}
    codes = sorted(f.code for f in findings)
    assert codes == ["drip_quantity_only", "pending_dku_confirmation"]


def test_rv2_mismatch_beyond_tolerance_is_error() -> None:
    bad = _event(amount=Decimal("13500.00"))  # 10000 * 1.25 = 12500 != 13500
    findings = validate_events([bad])
    assert [f.code for f in findings] == ["rv2_arithmetic_mismatch"]
    assert findings[0].severity == "error"


def test_rv2_nav_quantization_tolerance_passes_real_sell_shape() -> None:
    # Mirrors fixture SELL E0ef4747c2b: err ¥16.81 < |qty| * half-ulp(4dp) = ¥22.20
    sell = _event(
        event_id="Ebbbbbbbbbb",
        action="SELL",
        quantity=Decimal("-444010.301"),
        nav=Decimal("1.3808"),
        amount=Decimal("613072.61"),
    )
    assert validate_events([sell]) == []


def test_duplicate_event_id_is_error() -> None:
    findings = validate_events([_event(), _event()])
    assert any(f.code == "duplicate_event_id" and f.severity == "error" for f in findings)


def test_buy_missing_amount_is_error() -> None:
    findings = validate_events([_event(amount=None, nav=None)])
    assert [f.code for f in findings] == ["missing_required_field"]


def test_quantity_only_drip_is_info_not_error() -> None:
    drip = _event(action="DRIP", nav=None, amount=None)
    findings = validate_events([drip])
    assert [f.code for f in findings] == ["drip_quantity_only"]
    assert findings[0].severity == "info"


def test_pooled_buy_is_clean_and_missing_pool_on_deposit_is_warning() -> None:
    # Spec 2026-06-11b: internal events funded by a non-default pool
    # carry the tag (the 专户 BUYs) — no lint. Vocabulary is free text.
    pooled_buy = _event(pool="非留本")
    deposit = _event(
        event_id="Ecccccccccc",
        fund_code="CASH",
        action="DEPOSIT",
        quantity=None,
        nav=None,
        amount=Decimal("1000.00"),
    )
    findings = validate_events([pooled_buy, deposit])
    codes = {f.code for f in findings}
    assert "pool_missing" in codes
    assert "pool_unexpected" not in codes


def test_unpriced_buy_is_info_not_error() -> None:
    # 专户 pre-statement shape (MANIFEST 2026-06-11b): amount-only BUY.
    zhuanhu = _event(quantity=None, nav=None, amount=Decimal("10000000.00"), pool="非留本")
    findings = validate_events([zhuanhu])
    assert [f.code for f in findings] == ["buy_unpriced"]
    assert findings[0].severity == "info"


def test_provisional_pool_strings_accepted() -> None:
    # Pool is free text by design (>=3-bucket partition pending DKU naming).
    future_bucket = _event(
        event_id="Edddddddddd",
        fund_code="CASH",
        action="DEPOSIT",
        quantity=None,
        nav=None,
        amount=Decimal("1000.00"),
        pool="专户",
    )
    assert validate_events([future_bucket]) == []


def test_cash_unit_action_is_error() -> None:
    bad = _event(fund_code="CASH")
    findings = validate_events([bad])
    assert any(f.code == "cash_unit_action" and f.severity == "error" for f in findings)


def test_non_cny_currency_is_warning() -> None:
    usd = _event(currency="USD")
    assert any(f.code == "non_cny_currency" for f in validate_events([usd]))


def test_pending_confirmation_markers_flagged() -> None:
    flagged = _event(note="GUESSED subscription fee")
    assert any(f.code == "pending_dku_confirmation" for f in validate_events([flagged]))
