"""Row-level ingest validations (the pure subset of R-V1..R-V5).

Covered here (no I/O, no replay state):

- R-V2 arithmetic: ``|quantity| * nav ~= |amount|`` with a
  nav-quantization-aware tolerance — nav is a rounded per-unit price
  (typically 4dp), so the achievable bound is ``|quantity| * half-ulp
  (nav)``; the fixture's real SELL rows sit ¥1-17 off at a ¥22 bound.
  An absolute floor (`rv2_abs_tol`) covers tiny-quantity rows.
- Required-field shape per action (BUY/SELL need quantity + amount;
  the quantity-only DRIP is first-class and tagged info, not error).
- pool placement (DEPOSIT/WITHDRAW only), CASH-row action sanity,
  duplicate event_ids, non-CNY currency, pending-confirmation note
  markers (GUESSED / tentative / CONFLICT).

NOT here (replay-level, prism-side): R-V1 cash-never-negative,
R-V4 redemption ≤ held. R-V5 (append-only hash check across ingests)
needs prior-ingest state and is deferred.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from decimal import Decimal

from .canonical import CASH_FUND_CODE, ActionLogEvent, RowFinding

_UNIT_ACTIONS = ("BUY", "SELL", "DRIP")
_POOL_ACTIONS = ("DEPOSIT", "WITHDRAW")
_PENDING_MARKERS = ("guessed", "tentative", "conflict")


def _half_ulp(value: Decimal) -> Decimal:
    """Half of the last decimal place of `value` as given (its quantum)."""
    exponent = value.as_tuple().exponent
    if not isinstance(exponent, int):
        return Decimal(0)
    return Decimal(1).scaleb(exponent) / 2


def validate_events(
    events: Sequence[ActionLogEvent],
    *,
    rv2_abs_tol: Decimal = Decimal("0.01"),
) -> list[RowFinding]:
    findings: list[RowFinding] = []

    id_counts = Counter(e.event_id for e in events)
    for event_id, count in sorted(id_counts.items()):
        if count > 1:
            findings.append(
                RowFinding(
                    event_id=event_id,
                    code="duplicate_event_id",
                    severity="error",
                    message=f"event_id {event_id} appears {count} times",
                    payload={"count": count},
                )
            )

    for event in events:
        is_quantity_only_drip = (
            event.action == "DRIP"
            and event.quantity is not None
            and event.nav is None
            and event.amount is None
        )
        if is_quantity_only_drip:
            findings.append(
                RowFinding(
                    event_id=event.event_id,
                    code="drip_quantity_only",
                    severity="info",
                    message=(
                        "quantity-only DRIP row (cumulative; first-class shape "
                        "per relay 2026-06-10) — units in, cash-neutral, zero cost"
                    ),
                    payload={"quantity": str(event.quantity)},
                )
            )
        elif event.action in _UNIT_ACTIONS and (event.quantity is None or event.amount is None):
            findings.append(
                RowFinding(
                    event_id=event.event_id,
                    code="missing_required_field",
                    severity="error",
                    message=(
                        f"{event.action} row missing "
                        f"{'quantity' if event.quantity is None else 'amount'}"
                    ),
                    payload={"action": event.action},
                )
            )

        if (
            event.quantity is not None
            and event.nav is not None
            and event.amount is not None
            and event.quantity != 0
        ):
            error = abs(abs(event.quantity) * event.nav - abs(event.amount))
            tolerance = max(rv2_abs_tol, abs(event.quantity) * _half_ulp(event.nav))
            if error > tolerance:
                findings.append(
                    RowFinding(
                        event_id=event.event_id,
                        code="rv2_arithmetic_mismatch",
                        severity="error",
                        message=(
                            f"|quantity|*nav differs from |amount| by {error} "
                            f"(tolerance {tolerance})"
                        ),
                        payload={
                            "quantity": str(event.quantity),
                            "nav": str(event.nav),
                            "amount": str(event.amount),
                            "error": str(error),
                            "tolerance": str(tolerance),
                        },
                    )
                )

        if event.pool is not None and event.action not in _POOL_ACTIONS:
            findings.append(
                RowFinding(
                    event_id=event.event_id,
                    code="pool_unexpected",
                    severity="warning",
                    message=f"pool={event.pool} on {event.action} (DEPOSIT/WITHDRAW only)",
                    payload={"action": event.action, "pool": event.pool},
                )
            )
        if event.pool is None and event.action in _POOL_ACTIONS:
            findings.append(
                RowFinding(
                    event_id=event.event_id,
                    code="pool_missing",
                    severity="warning",
                    message=f"{event.action} row without a pool tag",
                    payload={"action": event.action},
                )
            )

        if event.fund_code == CASH_FUND_CODE and event.action in _UNIT_ACTIONS:
            findings.append(
                RowFinding(
                    event_id=event.event_id,
                    code="cash_unit_action",
                    severity="error",
                    message=f"{event.action} posted against the CASH account",
                    payload={"action": event.action},
                )
            )

        if event.currency != "CNY":
            findings.append(
                RowFinding(
                    event_id=event.event_id,
                    code="non_cny_currency",
                    severity="warning",
                    message=f"currency={event.currency} (FX out of scope v1)",
                    payload={"currency": event.currency},
                )
            )

        note_lower = (event.note or "").lower()
        if any(marker in note_lower for marker in _PENDING_MARKERS):
            findings.append(
                RowFinding(
                    event_id=event.event_id,
                    code="pending_dku_confirmation",
                    severity="info",
                    message="row note carries a pending-confirmation marker",
                    payload={"note": event.note},
                )
            )

    return findings
