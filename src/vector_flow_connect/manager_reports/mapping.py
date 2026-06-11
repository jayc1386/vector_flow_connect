"""Vision-LLM payload → canonical events + observations rows.

Pure function: no I/O, no DataFrame writes, no LLM calls. The output is two
lists of dicts that the runner concatenates into source-specific parquets.

Events emitted here are **pre-attribution** (`lot_id=None`). The runner runs
them through `lot_attribution.pro_rata_split` to attach lot_ids and finalize
`event_id`. For clean funds (no PDF-disclosed events) this path is unused.
"""

from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime
from typing import Any

from .canonical import (
    EXTRACTOR_NAME,
    EXTRACTOR_VERSION,
    SCHEMA_VERSION,
    SOURCE_ID,
    empty_event,
)

EventType = str  # canonical: subscription / redemption / dividend / perf_fee


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    return datetime.strptime(s, "%Y-%m-%d").date()


def _month_end(year: int, month: int) -> date:
    return date(year, month, monthrange(year, month)[1])


def _observation(
    *,
    observation_type: str,
    fund_id: str,
    fund_code: str | None,
    source_fund_string: str,
    as_of: date,
    value: float | int | None,
    source_locator: str,
    key: str | None = None,
    notes_raw: str = "",
    source_artifact: str,
    source_artifact_hash: str,
    extracted_at: datetime,
) -> dict[str, Any]:
    return {
        "observation_type": observation_type,
        "fund_id": fund_id,
        "fund_code": fund_code,
        "source_fund_string": source_fund_string,
        "as_of": as_of,
        "value": value,
        "key": key,
        "notes_raw": notes_raw,
        "source_artifact": source_artifact,
        "source_artifact_hash": source_artifact_hash,
        "source_locator": source_locator,
        "source_id": SOURCE_ID,
        "extractor_name": EXTRACTOR_NAME,
        "extractor_version": EXTRACTOR_VERSION,
        "schema_version": SCHEMA_VERSION,
        "extracted_at": extracted_at,
    }


def _pack_notes(**kv: Any) -> str:
    parts = []
    for k, v in kv.items():
        if v is None or v == "":
            continue
        parts.append(f"{k}={v}")
    return ";".join(parts)


def _map_observations(
    payload: dict[str, Any],
    *,
    fund_id: str,
    fund_code: str | None,
    source_fund_string: str,
    period_end: date,
    source_artifact: str,
    source_artifact_hash: str,
    extracted_at: datetime,
) -> list[dict[str, Any]]:
    """Build the observation rows. ~30 rows for a typical 睿远 monthly report.

    Sub-keyed observations (top_holding × rank, sector × label,
    geographic × region) lift the sub-key into the `key` column +
    `source_locator` per prism canonical_provenance v1.0.0; the
    human-readable bits stay in `notes_raw`.
    """
    obs: list[dict[str, Any]] = []
    pg1 = "pdf:page=1"
    share_class = payload.get("share_class")
    share_class_note = _pack_notes(share_class=share_class)
    share_class_key = f"share_class={share_class}" if share_class else None
    share_class_locator = f"{pg1}, key={share_class_key}" if share_class_key else pg1

    common = {
        "fund_id": fund_id,
        "fund_code": fund_code,
        "source_fund_string": source_fund_string,
        "source_artifact": source_artifact,
        "source_artifact_hash": source_artifact_hash,
        "extracted_at": extracted_at,
    }

    nav = payload.get("nav_per_unit")
    if nav is not None:
        obs.append(
            _observation(
                observation_type="nav_per_unit",
                as_of=period_end,
                value=float(nav),
                source_locator=share_class_locator,
                key=share_class_key,
                notes_raw=share_class_note,
                **common,
            )
        )

    nav_cum = payload.get("nav_cumulative")
    if nav_cum is not None:
        obs.append(
            _observation(
                observation_type="nav_cumulative",
                as_of=period_end,
                value=float(nav_cum),
                source_locator=share_class_locator,
                key=share_class_key,
                notes_raw=share_class_note,
                **common,
            )
        )

    sir = payload.get("since_inception_return_pct")
    if sir is not None:
        obs.append(
            _observation(
                observation_type="since_inception_return_pct",
                as_of=period_end,
                value=float(sir),
                source_locator=pg1,
                **common,
            )
        )

    for row in payload.get("monthly_returns") or []:
        rp = row.get("return_pct")
        if rp is None:
            continue
        try:
            y, m = int(row["year"]), int(row["month"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (1 <= m <= 12):
            # Some managers' return matrix has an annual-total column the
            # model sometimes records as month=13. Skip those rows — annual
            # totals are recoverable from the 12 monthly cells.
            continue
        obs.append(
            _observation(
                observation_type="monthly_return_pct",
                as_of=_month_end(y, m),
                value=float(rp),
                source_locator=pg1,
                **common,
            )
        )

    for row in payload.get("top_holdings") or []:
        rank = int(row["rank"])
        weight = row.get("weight_pct")
        if weight is None:
            continue
        key = f"top_holding.rank={rank}"
        locator = f"{pg1}, key={key}"
        notes = _pack_notes(
            security=row.get("security_name_zh"),
            ticker=row.get("ticker"),
        )
        obs.append(
            _observation(
                observation_type="top_holding_weight_pct",
                as_of=period_end,
                value=float(weight),
                source_locator=locator,
                key=key,
                notes_raw=notes,
                **common,
            )
        )
        prior = row.get("weight_pct_prior")
        if prior is not None:
            obs.append(
                _observation(
                    observation_type="top_holding_weight_pct_prior",
                    as_of=period_end,
                    value=float(prior),
                    source_locator=locator,
                    key=key,
                    notes_raw=notes,
                    **common,
                )
            )

    for row in payload.get("sector_breakdown") or []:
        w = row.get("weight_pct")
        if w is None:
            continue
        label = row.get("sector_label_zh")
        key = f"sector.label={label}" if label else None
        locator = f"{pg1}, key={key}" if key else pg1
        obs.append(
            _observation(
                observation_type="sector_weight_pct",
                as_of=period_end,
                value=float(w),
                source_locator=locator,
                key=key,
                notes_raw=_pack_notes(taxonomy=row.get("taxonomy_hint")),
                **common,
            )
        )

    for row in payload.get("geographic_breakdown") or []:
        w = row.get("weight_pct")
        if w is None:
            continue
        region = row.get("region_label_zh")
        key = f"geographic.region={region}" if region else None
        locator = f"{pg1}, key={key}" if key else pg1
        obs.append(
            _observation(
                observation_type="geographic_weight_pct",
                as_of=period_end,
                value=float(w),
                source_locator=locator,
                key=key,
                **common,
            )
        )

    pc = payload.get("position_counts") or {}
    for k in ("long", "short", "net"):
        v = pc.get(k)
        if v is None:
            continue
        obs.append(
            _observation(
                observation_type=f"position_count_{k}",
                as_of=period_end,
                value=int(v),
                source_locator=pg1,
                **common,
            )
        )

    return obs


# PDF event types → canonical EventType + payout_form (for dividend variants).
_EVENT_TYPE_MAP: dict[str, tuple[str, str | None]] = {
    "distribution_cash": ("dividend", "cash"),
    "distribution_units": ("dividend", "reinvested"),
    "perf_fee": ("perf_fee", None),
}


def _map_events(
    payload: dict[str, Any],
    *,
    fund_id: str,
    fund_code: str | None,
    source_fund_string: str,
    source_artifact: str,
    source_artifact_hash: str,
    recorded_at: date,
    extracted_at: datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (raw_events, dropped) — raw_events have `lot_id=None`; the
    runner attributes lots before persisting."""
    raw_events: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []

    for evt in payload.get("events") or []:
        pdf_type = evt.get("event_type")
        mapping = _EVENT_TYPE_MAP.get(pdf_type)
        if mapping is None:
            dropped.append(
                {
                    "reason": "unsupported_event_type",
                    "pdf_event_type": pdf_type,
                    "notes_raw": evt.get("notes_raw", ""),
                }
            )
            continue
        canonical_type, payout_form = mapping
        ev_date = _parse_date(evt.get("event_date"))

        row = empty_event()
        row.update(
            event_type=canonical_type,
            fund_id=fund_id,
            fund_code=fund_code,
            source_fund_string=source_fund_string,
            lot_id=None,
            event_date=ev_date,
            units_delta=evt.get("units_delta"),
            cash_delta=evt.get("cash_delta"),
            per_unit_amount=evt.get("per_unit_amount"),
            payout_form=payout_form,
            currency="CNY",
            confidence=evt.get("confidence_self", "fuzzy"),
            data_quality_flag="clean",
            notes_raw=evt.get("notes_raw", ""),
            source_artifact=source_artifact,
            source_artifact_hash=source_artifact_hash,
            source_locator="pdf:page=1",
            source_id=SOURCE_ID,
            extractor_name=EXTRACTOR_NAME,
            extractor_version=EXTRACTOR_VERSION,
            schema_version=SCHEMA_VERSION,
            extracted_at=extracted_at,
            valid_from=ev_date,
            recorded_at=recorded_at,
        )
        raw_events.append(row)

    return raw_events, dropped


def payload_to_canonical(
    payload: dict[str, Any],
    *,
    fund_id: str,
    source_fund_string: str,
    source_artifact: str,
    source_artifact_hash: str,
    extracted_at: datetime,
) -> dict[str, list[dict[str, Any]]]:
    """Convert a validated `record_monthly_report` payload to canonical rows.

    Returns `{"observations": [...], "raw_events": [...], "dropped": [...]}`.
    `raw_events` have `lot_id=None` and no `event_id` — the runner finalizes
    both via `lot_attribution.pro_rata_split` before writing to parquet.

    `fund_code` is pulled from the LLM payload if present (CSRC / AMAC
    code the model recognised in the PDF) — preserved per the contract.
    """
    period_end = _parse_date(payload["report_period_end"])
    if period_end is None:
        raise ValueError("payload['report_period_end'] is required")

    fund_code = payload.get("fund_code")

    observations = _map_observations(
        payload,
        fund_id=fund_id,
        fund_code=fund_code,
        source_fund_string=source_fund_string,
        period_end=period_end,
        source_artifact=source_artifact,
        source_artifact_hash=source_artifact_hash,
        extracted_at=extracted_at,
    )
    raw_events, dropped = _map_events(
        payload,
        fund_id=fund_id,
        fund_code=fund_code,
        source_fund_string=source_fund_string,
        source_artifact=source_artifact,
        source_artifact_hash=source_artifact_hash,
        recorded_at=period_end,
        extracted_at=extracted_at,
    )

    return {
        "observations": observations,
        "raw_events": raw_events,
        "dropped": dropped,
    }
