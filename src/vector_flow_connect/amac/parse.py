"""Pure-function parsers for AMAC API JSON and detail-page HTML.

No I/O; no network. All inputs are strings or already-parsed dicts.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from vector_flow_connect.amac._selectors import DETAIL_LABEL_MAP, FUND_DETAIL_BASE
from vector_flow_connect.amac.schema import SCHEMA_VERSION, SOURCE_ID, AMACRecord

# ---------- API parsers ----------


def _epoch_ms_to_iso_date(ms: Any) -> str | None:
    if ms is None:
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).date().isoformat()
    except (TypeError, ValueError):
        return None


def parse_search_response(payload: dict, scraped_at: str | None = None) -> list[AMACRecord]:
    """Convert a single page of `POST /api/pof/fund` JSON to AMACRecord rows.

    Captures only fields available from the index API. Detail-only fields
    (filing_stage, fund_type, …) stay absent; callers may enrich via
    `parse_detail_html`.
    """
    now = scraped_at or datetime.now(timezone.utc).isoformat()
    rows: list[AMACRecord] = []
    for r in payload.get("content", []):
        rec: AMACRecord = {
            "internal_id": str(r.get("id") or ""),
            "fund_no": r.get("fundNo") or "",
            "fund_name": r.get("fundName") or "",
            "manager_name": r.get("managerName") or "",
            "manager_type": r.get("managerType"),
            "working_state": r.get("workingState"),
            "put_on_record_date": _epoch_ms_to_iso_date(r.get("putOnRecordDate")),
            "establish_date": _epoch_ms_to_iso_date(r.get("establishDate")),
            "is_depute_manage": r.get("isDeputeManage"),
            "last_quarter_update": r.get("lastQuarterUpdate"),
            "detail_url": f"{FUND_DETAIL_BASE}/{r['url']}" if r.get("url") else None,
            "manager_url": r.get("managerUrl"),
            "mandator_name": r.get("mandatorName"),
            "managers_info_json": json.dumps(r["managersInfo"], ensure_ascii=False)
            if r.get("managersInfo")
            else None,
            "scraped_at": now,
            "schema_version": SCHEMA_VERSION,
            "source_id": SOURCE_ID,
        }
        rows.append(rec)
    return rows


def parse_pagination(payload: dict) -> dict:
    """Extract the Spring Page envelope (without content)."""
    return {
        "page": payload.get("number"),
        "size": payload.get("size"),
        "total_elements": payload.get("totalElements"),
        "total_pages": payload.get("totalPages"),
        "first": payload.get("first"),
        "last": payload.get("last"),
    }


# ---------- Detail-page HTML parser ----------

_TAG_RE = re.compile(r"<[^>]+>")
_TR_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.DOTALL | re.IGNORECASE)
_CELL_RE = re.compile(r"<t[hd][^>]*>(.*?)</t[hd]>", re.DOTALL | re.IGNORECASE)
_WS_COLLAPSE = re.compile(r"\s+")


def _strip_html(s: str) -> str:
    """Strip tags + entities, collapse whitespace."""
    text = _TAG_RE.sub("", s)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    return _WS_COLLAPSE.sub(" ", text).strip()


def parse_detail_html(html: str) -> dict[str, str]:
    """Extract a partial AMACRecord from one detail page.

    Returns only the detail-page-derived fields (those in DETAIL_LABEL_MAP).
    Caller merges with API row.
    """
    out: dict[str, str] = {}
    for row in _TR_RE.findall(html):
        cells = [_strip_html(c) for c in _CELL_RE.findall(row)]
        cells = [c for c in cells if c]
        if len(cells) < 2:
            continue
        label = cells[0].rstrip(":：").strip()
        if label not in DETAIL_LABEL_MAP:
            continue
        key = DETAIL_LABEL_MAP[label]
        value = cells[1] if len(cells) == 2 else " | ".join(cells[1:])
        out[key] = value
    return out


def merge_detail_into_record(record: AMACRecord, detail: dict[str, str]) -> AMACRecord:
    """Merge detail-page fields into an API-derived AMACRecord.

    Detail fields fill empty/missing slots; conflicting non-empty API values
    are NOT overwritten (API row is the canonical source for overlap fields
    like fund_name/fund_no/establish_date/put_on_record_date).
    """
    merged: AMACRecord = dict(record)  # type: ignore[assignment]
    for k, v in detail.items():
        if not merged.get(k):
            merged[k] = v  # type: ignore[literal-required]
    return merged
