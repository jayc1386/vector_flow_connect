"""Tests for OCC helpers (parse / generate / Friday + strike enumeration)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from vector_flow_connect.alpaca.occ import (
    friday_expirations,
    generate_occ_symbol,
    parse_occ_symbol,
    strikes_in_band,
)


class TestParseOccSymbol:
    def test_basic_call(self):
        root, exp, right, strike = parse_occ_symbol("SPY240920C00450000")
        assert root == "SPY"
        assert exp == date(2024, 9, 20)
        assert right == "C"
        assert strike == Decimal("450")

    def test_basic_put(self):
        _, _, right, strike = parse_occ_symbol("SPY240920P00450000")
        assert right == "P"
        assert strike == Decimal("450")

    def test_fractional_strike(self):
        # 7-digit-cent precision: $123.456 → 123456 → 00123456 with strike*1000
        _, _, _, strike = parse_occ_symbol("SPY240920C00123456")
        assert strike == Decimal("123.456")

    def test_multi_char_root(self):
        root, _, _, _ = parse_occ_symbol("AAPL240920C00150000")
        assert root == "AAPL"

    def test_malformed_raises(self):
        with pytest.raises(ValueError, match="malformed OCC"):
            parse_occ_symbol("notasymbol")

    def test_malformed_short_strike_raises(self):
        with pytest.raises(ValueError, match="malformed OCC"):
            parse_occ_symbol("SPY240920C0045000")  # 7-digit strike


class TestGenerateOccSymbol:
    def test_basic(self):
        sym = generate_occ_symbol("SPY", date(2024, 9, 20), "C", Decimal("450"))
        assert sym == "SPY240920C00450000"

    def test_roundtrip(self):
        sym = generate_occ_symbol("SPY", date(2024, 9, 20), "P", Decimal("450"))
        root, exp, right, strike = parse_occ_symbol(sym)
        assert root == "SPY"
        assert exp == date(2024, 9, 20)
        assert right == "P"
        assert strike == Decimal("450")

    def test_roundtrip_fractional(self):
        sym = generate_occ_symbol("SPY", date(2025, 1, 17), "C", Decimal("123.456"))
        _, _, _, strike = parse_occ_symbol(sym)
        assert strike == Decimal("123.456")

    def test_invalid_right_raises(self):
        with pytest.raises(ValueError, match="right must be"):
            generate_occ_symbol("SPY", date(2024, 9, 20), "X", Decimal("450"))

    def test_negative_strike_raises(self):
        with pytest.raises(ValueError, match="non-negative"):
            generate_occ_symbol("SPY", date(2024, 9, 20), "C", Decimal("-1"))

    def test_overflow_strike_raises(self):
        with pytest.raises(ValueError, match="8-digit OCC capacity"):
            generate_occ_symbol("SPY", date(2024, 9, 20), "C", Decimal("100000"))


class TestFridayExpirations:
    def test_september_2024(self):
        fridays = friday_expirations(date(2024, 9, 1), date(2024, 9, 30))
        assert fridays == (
            date(2024, 9, 6),
            date(2024, 9, 13),
            date(2024, 9, 20),
            date(2024, 9, 27),
        )

    def test_starts_on_friday(self):
        # 2024-09-06 is a Friday — should be included.
        fridays = friday_expirations(date(2024, 9, 6), date(2024, 9, 13))
        assert fridays == (date(2024, 9, 6), date(2024, 9, 13))

    def test_empty_range(self):
        assert friday_expirations(date(2024, 9, 30), date(2024, 9, 1)) == ()

    def test_no_friday_in_range(self):
        # Mon..Thu (2024-09-02 .. 2024-09-05)
        assert friday_expirations(date(2024, 9, 2), date(2024, 9, 5)) == ()


class TestStrikesInBand:
    def test_round_number_close(self):
        # close=450, band=10% → range [405, 495]; integer $1 increment.
        strikes = strikes_in_band(Decimal("450.0"), Decimal("0.10"))
        assert strikes[0] == Decimal("405")
        assert strikes[-1] == Decimal("495")
        assert len(strikes) == 91  # 405..495 inclusive

    def test_boundary_outward_expansion(self):
        # The Plan 0023 case: Decimal // would truncate toward zero rather
        # than floor toward -inf, silently missing the upper boundary.
        # close=450.5, band=10% → range [405.45, 495.55].
        # Lower floors to 405; upper ceilings to 496 (not 495 — that would
        # leave the band partially uncovered).
        strikes = strikes_in_band(Decimal("450.5"), Decimal("0.10"))
        assert strikes[0] == Decimal("405")
        assert strikes[-1] == Decimal("496")

    def test_band_at_upper_limit(self):
        # band_pct = 1 (100%) is the edge of the allowed range.
        strikes = strikes_in_band(Decimal("100"), Decimal("1"))
        assert strikes[0] == Decimal("0")
        assert strikes[-1] == Decimal("200")

    def test_invalid_close_raises(self):
        with pytest.raises(ValueError, match="close_price must be positive"):
            strikes_in_band(Decimal("0"), Decimal("0.1"))

    def test_invalid_band_pct_raises(self):
        with pytest.raises(ValueError, match=r"band_pct must be in"):
            strikes_in_band(Decimal("100"), Decimal("1.5"))
        with pytest.raises(ValueError, match=r"band_pct must be in"):
            strikes_in_band(Decimal("100"), Decimal("0"))

    def test_invalid_increment_raises(self):
        with pytest.raises(ValueError, match="increment must be positive"):
            strikes_in_band(Decimal("100"), Decimal("0.1"), increment=Decimal("0"))

    def test_custom_increment(self):
        # $5 spacing far OTM: close=450, band=10% → [405, 495] with $5 steps.
        strikes = strikes_in_band(Decimal("450"), Decimal("0.10"), increment=Decimal("5"))
        assert strikes[0] == Decimal("405")
        assert strikes[-1] == Decimal("495")
        # Step should be 5 between consecutive strikes.
        assert strikes[1] - strikes[0] == Decimal("5")
