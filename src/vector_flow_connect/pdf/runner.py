"""Orchestrate: inventory → render → LLM (cached) → map → audit → write.

Architecture notes:
- First PDF runs sequentially to warm Anthropic's ephemeral prompt cache
  (~2.6k system tokens). Subsequent PDFs run in parallel (ThreadPoolExecutor,
  6 workers) — most hit the warm cache via cache_read, dropping per-call
  cost ~90% on the cached portion.
- Each PDF is fully isolated in try/except — one malformed/unreadable PDF
  cannot kill the run. Per-PDF failures land in `issues.json`.
- Fund identity is intentionally stub-keyed here; `scripts/consolidate.py`
  is responsible for unifying fund_ids via the seed CSV.
"""

from __future__ import annotations

import json
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .audit import audit_numeric_fields
from .canonical import (
    AUDIT_DISCREPANCY_COLUMNS,
    EVENT_COLUMNS,
    EXTRACTOR_NAME,
    EXTRACTOR_VERSION,
    OBSERVATION_COLUMNS,
    PDF_EXTRACTOR_VERSION,
    SCHEMA_VERSION,
    SOURCE_ID,
    fund_id_stub,
)
from .inventory import filter_by_token, scan
from .llm_client import extract_pdf_cached
from .mapping import payload_to_canonical
from .multifund import detect_fund_pages
from .name_normalize import normalize_fund_name

# Sonnet 4.6 list-price USD per token (as of 2026-05); update if pricing changes.
_PRICE_INPUT_PER_TOKEN = 3.0 / 1_000_000
_PRICE_OUTPUT_PER_TOKEN = 15.0 / 1_000_000
_PRICE_CACHE_WRITE_PER_TOKEN = 3.75 / 1_000_000  # 1.25x input
_PRICE_CACHE_READ_PER_TOKEN = 0.30 / 1_000_000  # 0.1x input
_MAX_WORKERS = 6


def _estimate_cost_usd(usage: dict[str, Any]) -> float:
    """Estimate Anthropic spend for one call from the usage dict."""
    return (
        usage.get("input_tokens", 0) * _PRICE_INPUT_PER_TOKEN
        + usage.get("output_tokens", 0) * _PRICE_OUTPUT_PER_TOKEN
        + usage.get("cache_creation_input_tokens", 0) * _PRICE_CACHE_WRITE_PER_TOKEN
        + usage.get("cache_read_input_tokens", 0) * _PRICE_CACHE_READ_PER_TOKEN
    )


def _coerce_event_df(events: list[dict[str, Any]]) -> pd.DataFrame:
    return (
        pd.DataFrame(events, columns=EVENT_COLUMNS)
        if events
        else pd.DataFrame(columns=EVENT_COLUMNS)
    )


def _coerce_obs_df(observations: list[dict[str, Any]]) -> pd.DataFrame:
    return (
        pd.DataFrame(observations, columns=OBSERVATION_COLUMNS)
        if observations
        else pd.DataFrame(columns=OBSERVATION_COLUMNS)
    )


def _retag_page_locators(obs: list[dict[str, Any]], first_page: int) -> None:
    """For multi-fund extractions, rewrite `source_locator` so a fund extracted
    from page N of a compilation PDF says `pdf:page=N` (not the mapping's default
    `pdf:page=1`)."""
    if first_page == 1:
        return
    page_2 = first_page + 1
    for row in obs:
        loc = row.get("source_locator")
        if loc == "pdf:page=1":
            row["source_locator"] = f"pdf:page={first_page}"
        elif loc == "pdf:page=2":
            row["source_locator"] = f"pdf:page={page_2}"


def _process_extraction(
    pdf_path: Path,
    row: dict[str, Any],
    *,
    cache_dir: Path,
    extractor_version: str,
    bypass_cache: bool,
    extracted_at: datetime,
    pages: list[int] | None,
    fund_hint: str | None = None,
) -> dict[str, Any]:
    """Run one extraction call (one fund or one full PDF). Pure single-fund path."""
    out: dict[str, Any] = {
        "row": row,
        "pdf_path": str(pdf_path),
        "pages": pages,
        "fund_hint": fund_hint,
        "ok": False,
        "observations": [],
        "raw_events": [],
        "dropped": [],
        "discrepancies": [],
        "error": None,
        "artifact_info": None,
    }
    try:
        result, cache_info = extract_pdf_cached(
            pdf_path,
            cache_dir=cache_dir,
            extractor_version=extractor_version,
            bypass_cache=bypass_cache,
            pages=pages,
        )
        payload = result.payload
        llm_name = payload.get("fund_name_zh", "") or ""
        # Detector-overrides-LLM for fund name: when pdfplumber's text-layer
        # heuristic identified a fund header (multifund split path), prefer
        # it. Vision occasionally misreads visually-similar characters
        # (汯/泓, 鞅/鲅/鲧) while the text-layer extraction is
        # character-stable. For single-fund PDFs `fund_hint` is None and
        # we fall through to the LLM emission as before.
        fund_name_zh = fund_hint or llm_name
        normalized = normalize_fund_name(fund_name_zh)
        fund_id = fund_id_stub(normalized)
        source_fund_string = normalized or fund_name_zh

        canonical = payload_to_canonical(
            payload,
            fund_id=fund_id,
            source_fund_string=source_fund_string,
            source_artifact=pdf_path.name,
            source_artifact_hash=cache_info["artifact_hash"],
            extracted_at=extracted_at,
        )
        # For multi-fund: rewrite source_locator to point at the actual pages
        if pages:
            _retag_page_locators(canonical["observations"], pages[0])

        discrepancies = audit_numeric_fields(payload, pdf_path)

        out.update(
            ok=True,
            observations=canonical["observations"],
            raw_events=canonical["raw_events"],
            dropped=canonical["dropped"],
            discrepancies=discrepancies,
            artifact_info={
                "filename": pdf_path.name,
                "parent_dir": row["parent_dir"],
                "period_end": row["period_end"].isoformat() if row["period_end"] else None,
                "pages": pages,
                "multifund_hint": fund_hint,
                "fund_name_zh_extracted": fund_name_zh,
                "fund_id_stub": fund_id,
                "artifact_hash": cache_info["artifact_hash"],
                "cache_hit": cache_info["hit"],
                "extractor_version": extractor_version,
                "usage": result.usage,
                "estimated_cost_usd": _estimate_cost_usd(result.usage),
                "n_observations": len(canonical["observations"]),
                "n_raw_events": len(canonical["raw_events"]),
                "n_discrepancies": len(discrepancies),
                "manager_commentary_summary": payload.get("manager_commentary_summary"),
                "extraction_notes": payload.get("extraction_notes", ""),
            },
        )
    except Exception as exc:
        out["error"] = {
            "kind": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
        }
    return out


def _process_one(
    row: dict[str, Any],
    *,
    cache_dir: Path,
    extractor_version: str,
    bypass_cache: bool,
    extracted_at: datetime,
) -> list[dict[str, Any]]:
    """Run extraction for one PDF — may yield 1 result (single-fund / fallback)
    or N results (multi-fund compilation, one per detected fund page range)."""
    pdf_path = Path(row["pdf_path"])
    is_multifund = bool(row.get("is_multifund_candidate", False))
    if is_multifund:
        try:
            ranges = detect_fund_pages(pdf_path)
        except Exception:
            ranges = []
        fund_ranges = [r for r in ranges if r.role == "fund" and r.fund_hint is not None]
        if len(fund_ranges) > 1:
            return [
                _process_extraction(
                    pdf_path,
                    row,
                    cache_dir=cache_dir,
                    extractor_version=extractor_version,
                    bypass_cache=bypass_cache,
                    extracted_at=extracted_at,
                    pages=r.pages,
                    fund_hint=r.fund_hint,
                )
                for r in fund_ranges
            ]
        # Filename hinted multi-fund but detector found 0 or 1 boundaries —
        # fall back to a single full-PDF extraction (uses legacy cache key).
    return [
        _process_extraction(
            pdf_path,
            row,
            cache_dir=cache_dir,
            extractor_version=extractor_version,
            bypass_cache=bypass_cache,
            extracted_at=extracted_at,
            pages=None,
        )
    ]


def extract_dir(
    sample_inputs_root: str | Path,
    out_dir: str | Path,
    *,
    fund_filter: str | None = "睿远",
    extractor_version: str = PDF_EXTRACTOR_VERSION,
    bypass_cache: bool = False,
    max_workers: int = _MAX_WORKERS,
) -> dict[str, Any]:
    """Run the PDF pipeline over every PDF in `sample_inputs_root` that
    matches `fund_filter` (None = all)."""
    sample_inputs_root = Path(sample_inputs_root)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = out_dir / "raw_responses"

    inventory = scan(sample_inputs_root)
    if fund_filter:
        inventory = filter_by_token(inventory, fund_filter)
    rows: list[dict[str, Any]] = [
        {col: row[col] for col in inventory.columns} for _, row in inventory.iterrows()
    ]

    extracted_at = datetime.now(timezone.utc)
    results: list[dict[str, Any]] = []

    if rows:
        # First PDF sequentially → warms the 5-min prompt cache for the rest.
        results.extend(
            _process_one(
                rows[0],
                cache_dir=cache_dir,
                extractor_version=extractor_version,
                bypass_cache=bypass_cache,
                extracted_at=extracted_at,
            )
        )
        if len(rows) > 1:
            with ThreadPoolExecutor(max_workers=min(max_workers, len(rows) - 1)) as ex:
                futures = [
                    ex.submit(
                        _process_one,
                        r,
                        cache_dir=cache_dir,
                        extractor_version=extractor_version,
                        bypass_cache=bypass_cache,
                        extracted_at=extracted_at,
                    )
                    for r in rows[1:]
                ]
                for f in as_completed(futures):
                    results.extend(f.result())

    all_events: list[dict[str, Any]] = []
    all_obs: list[dict[str, Any]] = []
    all_discrepancies: list[dict[str, Any]] = []
    all_issues: list[dict[str, Any]] = []
    per_artifact: list[dict[str, Any]] = []

    for r in results:
        if not r["ok"]:
            all_issues.append(
                {
                    "source_artifact": Path(r["pdf_path"]).name,
                    "kind": "extraction_failed",
                    "error": r["error"],
                }
            )
            continue
        all_obs.extend(r["observations"])
        all_events.extend(r["raw_events"])
        for d in r["dropped"]:
            all_issues.append(
                {
                    "source_artifact": Path(r["pdf_path"]).name,
                    "kind": "dropped_event",
                    **d,
                }
            )
        for disc in r["discrepancies"]:
            all_discrepancies.append(
                {
                    "source_artifact": Path(r["pdf_path"]).name,
                    "source_artifact_hash": r["artifact_info"]["artifact_hash"],
                    "field_path": disc.field_path,
                    "expected_value": disc.expected_value,
                    "nearest_pdf_value": disc.nearest_pdf_value,
                    "nearest_diff": disc.nearest_diff,
                    "source_id": SOURCE_ID,
                    "extractor_name": EXTRACTOR_NAME,
                    "extractor_version": EXTRACTOR_VERSION,
                    "schema_version": SCHEMA_VERSION,
                    "extracted_at": datetime.now(timezone.utc).isoformat(),
                }
            )
        per_artifact.append(r["artifact_info"])

    events_df = _coerce_event_df(all_events)
    obs_df = _coerce_obs_df(all_obs)
    discrepancies_df = (
        pd.DataFrame(all_discrepancies, columns=AUDIT_DISCREPANCY_COLUMNS)
        if all_discrepancies
        else pd.DataFrame(columns=AUDIT_DISCREPANCY_COLUMNS)
    )

    events_df.to_parquet(out_dir / "events.parquet", index=False)
    obs_df.to_parquet(out_dir / "observations.parquet", index=False)
    discrepancies_df.to_parquet(out_dir / "audit_discrepancies.parquet", index=False)
    (out_dir / "issues.json").write_text(
        json.dumps(all_issues, ensure_ascii=False, indent=2, default=str)
    )

    total_input = sum(a["usage"].get("input_tokens", 0) for a in per_artifact)
    total_output = sum(a["usage"].get("output_tokens", 0) for a in per_artifact)
    total_cache_write = sum(a["usage"].get("cache_creation_input_tokens", 0) for a in per_artifact)
    total_cache_read = sum(a["usage"].get("cache_read_input_tokens", 0) for a in per_artifact)
    total_cost = sum(a["estimated_cost_usd"] for a in per_artifact)

    manifest = {
        "extractor_name": EXTRACTOR_NAME,
        "extractor_version": extractor_version,
        "extracted_at": extracted_at.isoformat(),
        "sample_inputs_root": str(sample_inputs_root),
        "fund_filter": fund_filter,
        "n_pdfs": len(rows),
        "n_artifacts": len(per_artifact),
        "n_failed": sum(1 for r in results if not r["ok"]),
        "n_observations": len(obs_df),
        "n_raw_events": len(events_df),
        "n_audit_discrepancies": len(discrepancies_df),
        "cache_hits": sum(1 for a in per_artifact if a["cache_hit"]),
        "cost_summary": {
            "total_input_tokens": int(total_input),
            "total_output_tokens": int(total_output),
            "total_cache_write_tokens": int(total_cache_write),
            "total_cache_read_tokens": int(total_cache_read),
            "estimated_total_usd": round(total_cost, 4),
            "avg_per_artifact_usd": (
                round(total_cost / len(per_artifact), 4) if per_artifact else 0
            ),
        },
        "artifacts": per_artifact,
    }
    (out_dir / "extraction_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str)
    )
    return manifest
