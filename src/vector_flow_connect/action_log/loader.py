"""CSV loader for the action log.

Strict on schema (column drift is a contract break → raise); row
values are coerced into `ActionLogEvent` and a row that cannot
construct the model at all (bad action, bad date) also raises with
row context — semantic checks beyond model shape live in
`validate.py` and yield findings instead.
"""

from __future__ import annotations

import csv
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from pydantic import ValidationError

from .canonical import COLUMNS, ActionLogEvent

_DECIMAL_FIELDS = ("quantity", "nav", "amount")


class ActionLogSchemaError(ValueError):
    """Raised when the CSV header or a row breaks the ACTION_LOG_SPEC schema."""


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def load_action_log(path: str | Path) -> list[ActionLogEvent]:
    """Load the action-log CSV (utf-8-sig, file order preserved)."""
    path = Path(path)
    events: list[ActionLogEvent] = []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = [f.strip() for f in (reader.fieldnames or [])]
        if fieldnames != COLUMNS:
            missing = [c for c in COLUMNS if c not in fieldnames]
            unknown = [c for c in fieldnames if c not in COLUMNS]
            raise ActionLogSchemaError(
                f"action_log header drift in {path.name}: "
                f"missing={missing} unknown={unknown} (got {fieldnames})"
            )
        for lineno, row in enumerate(reader, start=2):
            cleaned: dict[str, object] = {k: _clean(v) for k, v in row.items()}
            for field in _DECIMAL_FIELDS:
                raw = cleaned[field]
                if raw is not None:
                    try:
                        cleaned[field] = Decimal(str(raw))
                    except InvalidOperation as exc:
                        raise ActionLogSchemaError(
                            f"{path.name}:{lineno} non-numeric {field}={raw!r}"
                        ) from exc
            raw_date = cleaned["event_date"]
            if raw_date is not None:
                try:
                    cleaned["event_date"] = date.fromisoformat(str(raw_date))
                except ValueError as exc:
                    raise ActionLogSchemaError(
                        f"{path.name}:{lineno} bad event_date={raw_date!r}"
                    ) from exc
            if cleaned["currency"] is None:
                cleaned["currency"] = "CNY"
            try:
                events.append(ActionLogEvent(**cleaned))  # type: ignore[arg-type]
            except ValidationError as exc:
                raise ActionLogSchemaError(
                    f"{path.name}:{lineno} row failed model validation "
                    f"(event_id={cleaned.get('event_id')!r}): {exc}"
                ) from exc
    return events
