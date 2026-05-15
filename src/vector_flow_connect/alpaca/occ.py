"""OCC option-symbol parse/generate + enumeration helpers.

OCC symbol layout: `<root><YYMMDD><C|P><strike*1000 8-digit>`.
Example: `SPY240920C00450000` → root SPY, 2024-09-20, Call, $450.000.
Alpaca returns symbols without OCC's 6-char root padding.

`friday_expirations` + `strikes_in_band` drive the OCC-enumeration
backfill path (originally landed in prism Plan 0023): generate every
Friday in a window x strikes in a price band, instead of relying on
the live-chain snapshot which omits already-expired contracts.

Decimal-quantize note: `Decimal.__floordiv__` truncates toward zero
rather than floor toward negative infinity, which silently misses one
strike at the upper boundary on negative inputs. `strikes_in_band`
uses `.quantize(...)` with explicit `ROUND_FLOOR` / `ROUND_CEILING`
modes to avoid this.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

_OCC_RE = re.compile(r"^([A-Z]+)(\d{6})([CP])(\d{8})$")


def parse_occ_symbol(symbol: str) -> tuple[str, date, str, Decimal]:
    """Parse an OCC symbol into `(root, expiration, right, strike)`.

    Raises `ValueError` on malformed input.
    """
    m = _OCC_RE.match(symbol)
    if m is None:
        raise ValueError(f"malformed OCC symbol: {symbol!r}")
    root, yymmdd, right, strike_raw = m.groups()
    expiration = datetime.strptime(yymmdd, "%y%m%d").date()
    # Strike stored as integer thousandths; 8-digit format supports up to $99,999.999.
    strike = Decimal(strike_raw) / Decimal(1000)
    return root, expiration, right, strike


def generate_occ_symbol(
    underlying: str,
    expiration: date,
    right: str,
    strike: Decimal,
) -> str:
    """Canonical OCC option-symbol string.

    Layout: `<root><YYMMDD><C|P><strike*1000 8-digit zero-padded>`.
    Example: `generate_occ_symbol("SPY", date(2024, 9, 20), "C",
    Decimal("450"))` → `"SPY240920C00450000"`.

    Round-trips through `parse_occ_symbol`. `strike` is multiplied by
    1000 and rendered as an 8-digit zero-padded integer, supporting
    fractional strikes down to $0.001.
    """
    if right not in ("C", "P"):
        raise ValueError(f"right must be 'C' or 'P', got {right!r}")
    if strike < 0:
        raise ValueError(f"strike must be non-negative, got {strike}")
    strike_int = int((strike * Decimal(1000)).to_integral_value())
    if strike_int >= 10**8:
        raise ValueError(f"strike {strike} exceeds 8-digit OCC capacity")
    return f"{underlying}{expiration.strftime('%y%m%d')}{right}{strike_int:08d}"


def friday_expirations(start: date, end: date) -> tuple[date, ...]:
    """Every Friday in `[start, end]`, ascending. Empty if `end < start`.

    Used by the OCC-enumeration backfill path to drive expiry generation
    without consulting the live chain. Standard monthly SPY options
    expire on the third Friday; weekly SPY options also expire on
    Fridays (plus Mon/Wed for SPY specifically, which v0 omits).
    """
    if end < start:
        return ()
    # Python weekday(): Mon=0, ..., Fri=4, Sat=5, Sun=6.
    offset_to_friday = (4 - start.weekday()) % 7
    first_friday = start + timedelta(days=offset_to_friday)
    out: list[date] = []
    cursor = first_friday
    while cursor <= end:
        out.append(cursor)
        cursor += timedelta(days=7)
    return tuple(out)


def strikes_in_band(
    close_price: Decimal,
    band_pct: Decimal,
    increment: Decimal = Decimal("1"),
) -> tuple[Decimal, ...]:
    """Strikes inside `[close * (1 - band_pct), close * (1 + band_pct)]`
    at `increment` spacing, anchored to integer multiples of `increment`,
    sorted ascending. Inclusive on both ends.

    Used by the OCC-enumeration backfill path to generate candidate
    strikes per expiration. SPY actually lists $1 spacing near the money
    and $5 far out — over-enumerating (always $1) is harmless because
    missing strikes return empty bars (skip-on-empty in the consumer).
    """
    if close_price <= 0:
        raise ValueError("close_price must be positive")
    if band_pct <= 0 or band_pct > 1:
        raise ValueError("band_pct must be in (0, 1]")
    if increment <= 0:
        raise ValueError("increment must be positive")
    lower = close_price * (Decimal(1) - band_pct)
    upper = close_price * (Decimal(1) + band_pct)
    # Snap to integer multiples of `increment`, expanding outward so the
    # band is fully covered (floor on the low side, ceiling on the high
    # side). Decimal's // truncates toward zero rather than floor toward
    # negative infinity, so use quantize() with the explicit rounding
    # modes instead.
    low_steps = int((lower / increment).quantize(Decimal("1"), rounding=ROUND_FLOOR))
    high_steps = int((upper / increment).quantize(Decimal("1"), rounding=ROUND_CEILING))
    out: list[Decimal] = []
    k = low_steps
    while k <= high_steps:
        out.append(increment * Decimal(k))
        k += 1
    return tuple(out)
