"""Top-level dispatcher for the master-record workbook.

Walks every sheet, dispatches to per-sheet parsers, dedupes events
across snapshots, builds the lots / funds stub registries, and writes
the canonical Parquet outputs plus a reconciliation report.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import openpyxl
import pandas as pd

from ._inherited_fund_resolver import build_resolver, resolver_to_dataframe_rows
from .annualized import parse_sheet as parse_annualized
from .canonical import (
    EVENT_COLUMNS,
    EXTRACTOR_VERSION,
    FUND_COLUMNS,
    LOT_COLUMNS,
    OBSERVATION_COLUMNS,
    POSITION_COLUMNS,
    SCHEMA_VERSION,
    SOURCE_ID,
    SourceContext,
    file_sha256,
)
from .events_sheet import (
    DEFAULT_SHEET_NAME as EVENTS_LOG_SHEET_NAME,
)
from .events_sheet import (
    events_sheet_present,
    parse_events_sheet,
)
from .snapshot import parse_sheet as parse_snapshot
from .snapshot import scan_fund_strings

SNAPSHOT_RE = re.compile(r"^\d{8}$")

# Default location of the per-tenant fund_code reference CSV (Bucket B).
# Resolved relative to the repository root so vfc bundles a sample DKU
# reference at install time; callers can override via the
# `fund_codes_reference` parameter to `extract()`.
DEFAULT_FUND_CODES_REFERENCE = (
    Path(__file__).resolve().parents[3] / "data" / "reference" / "fund_codes.csv"
)


def classify_sheet(name: str) -> str:
    if SNAPSHOT_RE.fullmatch(name):
        return "snapshot"
    if name == "市值动态":
        return "market_dynamics"
    if name == "收益回撤":
        return "returns_drawdown"
    if name == "收益年化":
        return "annualized"
    if name == "债权比较":
        return "debt_comparison"
    if name == EVENTS_LOG_SHEET_NAME:
        return "events_log"
    return "unknown"


def extract(
    workbook_path: str | Path,
    *,
    out_dir: str | Path = "data/canonical",
    fund_codes_reference: str | Path | None = None,
) -> dict:
    """Run the full extraction pipeline.

    Returns a dict with the in-memory DataFrames and a manifest. Writes
    {events,lots,funds,positions,asset_class_summary}.parquet and
    extraction_manifest.json into `out_dir`.
    """
    workbook_path = Path(workbook_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    artifact_hash = file_sha256(workbook_path)
    ctx = SourceContext(
        artifact=workbook_path.name,
        artifact_hash=artifact_hash,
        extracted_at=datetime.now(timezone.utc),
    )

    wb = openpyxl.load_workbook(workbook_path, read_only=True, data_only=True)

    sheet_classification: dict[str, str] = {s: classify_sheet(s) for s in wb.sheetnames}

    # --- Pass 1: scan every snapshot's fund-string column, build the
    # canonical-identity resolver. Cheap (one column per sheet).
    all_fund_strings: set[str] = set()
    for sheet_name, kind in sheet_classification.items():
        if kind != "snapshot":
            continue
        ws = wb[sheet_name]
        for s in scan_fund_strings(ws):
            all_fund_strings.add(s)

    resolver = build_resolver(all_fund_strings)

    # --- Pass 2: full snapshot parse, with the resolver wired in.
    events: list[dict] = []
    positions: list[dict] = []
    observations: list[dict] = []
    asset_summary: list[dict] = []
    issues: list[dict] = []
    annualized_result: dict | None = None

    for sheet_name, kind in sheet_classification.items():
        if kind == "snapshot":
            ws = wb[sheet_name]
            result = parse_snapshot(
                ws,
                sheet_name=sheet_name,
                ctx=ctx,
                resolve_fund_id=resolver.resolve,
            )
            events.extend(result["events"])
            positions.extend(result["positions"])
            observations.extend(result.get("observations", []))
            asset_summary.extend(result["asset_class_summary"])
            issues.extend(result["issues"])
        elif kind == "annualized":
            ws = wb[sheet_name]
            annualized_result = parse_annualized(ws)
        elif kind in ("market_dynamics", "returns_drawdown", "debt_comparison"):
            # v1: skipped — parsers deferred
            pass
        elif kind == "events_log":
            # Path A — parsed below after snapshot pass completes.
            pass
        else:
            issues.append(
                {
                    "sheet": sheet_name,
                    "locator": sheet_name,
                    "kind": "unknown_sheet",
                    "detail": "no parser registered",
                }
            )

    # --- Pass 3: Path A vs Path B event-source selection.
    #
    # Snapshot lot rows always emit subscription events (lot identity).
    # Non-subscription events (redemption / dividend / perf_fee) come
    # from one of two sources:
    #   - Path A (when `事件流水` sheet is present): the dedicated
    #     events-log sheet is the authoritative source. Notes-parser
    #     emissions are dropped to avoid double-counting.
    #   - Path B (legacy fallback, used when no events log exists):
    #     notes-parser emissions are kept and tagged with
    #     `data_quality_flag='derived_from_notes'` so downstream
    #     consumers can identify them as cumulative-notes-derived.
    events_path: str
    events_log_events: list[dict] = []
    if events_sheet_present(wb):
        events_path = "A"
        events_log_events = parse_events_sheet(
            wb,
            ctx=ctx,
            resolve_fund_id=resolver.resolve,
        )
        # Drop snapshot-emitted non-subscription events (notes-parser
        # products); keep subscriptions (lot identity).
        events = [evt for evt in events if evt.get("event_type") == "subscription"]
        events.extend(events_log_events)
    else:
        events_path = "B"
        # Tag every non-subscription event with the explicit lineage flag.
        for evt in events:
            if evt.get("event_type") != "subscription":
                evt["data_quality_flag"] = "derived_from_notes"

    wb.close()

    # --- fund_code resolution ---
    # Three tiers, evaluated in order:
    #   Tier 1: parens in `source_fund_string` (e.g. `华夏纯债债券A (000015)`)
    #   Bucket A: fund_id of form `fnd_<6-digit>` — recoverable from the
    #     resolver's clustering even if the first-seen event for that
    #     fund lacks parens
    #   Bucket B: static reference table for funds outside Tier 1/A
    #     (AMAC private funds, unverified CSRC, issuer-internal)
    # Tiers 1 + A are applied here at row-construction time. Bucket B is
    # joined into funds_df after it is built, then backfilled to row
    # tables via the fund_id → fund_code map.
    fund_id_code_re = re.compile(r"^fnd_(\d{6})$")

    def _resolve_fund_code(fund_id: str | None, source_fund_string: str | None) -> str | None:
        if source_fund_string:
            m = re.search(r"\((\d{4,6})\)", source_fund_string)
            if m:
                return m.group(1)
        if fund_id:
            m2 = fund_id_code_re.match(fund_id)
            if m2:
                return m2.group(1)
        return None

    for evt in events:
        if "fund_code" not in evt or evt.get("fund_code") is None:
            evt["fund_code"] = _resolve_fund_code(
                evt.get("fund_id"), evt.get("source_fund_string")
            )
    for pos in positions:
        if "fund_code" not in pos or pos.get("fund_code") is None:
            # positions don't carry source_fund_string — try fund_id directly,
            # remaining gaps fill in via the funds-join below.
            pos["fund_code"] = _resolve_fund_code(pos.get("fund_id"), None)

    # Dedupe events: same event_id may be produced from multiple
    # snapshots. Keep the row with the earliest `recorded_at`.
    events_df = (
        pd.DataFrame(events, columns=EVENT_COLUMNS)
        if events
        else pd.DataFrame(columns=EVENT_COLUMNS)
    )
    if not events_df.empty:
        events_df = (
            events_df.sort_values("recorded_at")
            .drop_duplicates(subset=["event_id"], keep="first")
            .reset_index(drop=True)
        )

    positions_df = (
        pd.DataFrame(positions, columns=POSITION_COLUMNS)
        if positions
        else pd.DataFrame(columns=POSITION_COLUMNS)
    )

    asset_summary_df = pd.DataFrame(asset_summary) if asset_summary else pd.DataFrame()

    # Build lots registry from subscription events.
    if not events_df.empty:
        subs = events_df[events_df["event_type"] == "subscription"].copy()
        lots_df = (
            pd.DataFrame(
                {
                    "lot_id": subs["lot_id"],
                    "fund_id": subs["fund_id"],
                    "fund_code": subs.get("fund_code"),
                    "subscription_date": subs["event_date"],
                    "subscription_event_id": subs["event_id"],
                    "initial_cost": -subs["cash_delta"],  # cash_delta is negative for subscriptions
                    "initial_units": subs["units_delta"],
                    "initial_nav": subs["per_unit_amount"],
                    "currency": subs["currency"],
                    "data_quality_flag": "clean",
                    "source_id": SOURCE_ID,
                    "schema_version": SCHEMA_VERSION,
                }
            )
            .drop_duplicates(subset=["lot_id"])
            .reset_index(drop=True)
        )
    else:
        lots_df = pd.DataFrame(columns=LOT_COLUMNS)

    # Build funds stub registry.
    if not positions_df.empty:
        fund_first_seen = positions_df.groupby("fund_id")["as_of"].min().to_dict()
        fund_last_seen = positions_df.groupby("fund_id")["as_of"].max().to_dict()
    else:
        fund_first_seen = {}
        fund_last_seen = {}

    if not events_df.empty:
        fund_rows: dict[str, dict] = {}
        for _, row in events_df.iterrows():
            fid = row["fund_id"]
            if fid in fund_rows:
                continue
            raw = row["source_fund_string"] or ""
            resolved = _resolve_fund_code(fid, raw)
            fund_rows[fid] = {
                "fund_id": fid,
                "source_fund_string": raw,
                "name_zh": re.sub(r"\(\d{4,6}\)", "", raw).strip(),
                "fund_code": resolved,
                # Codes derived from the source workbook (Tier 1 or Bucket A)
                # are confirmed; Bucket B's reference CSV may overlay its own
                # confidence below.
                "code_confidence": "confirmed" if resolved else None,
                "code_source": "CSRC" if resolved else None,
                "asset_class": None,
                "first_seen_as_of": fund_first_seen.get(fid),
                "last_seen_as_of": fund_last_seen.get(fid),
                "source_id": SOURCE_ID,
                "schema_version": SCHEMA_VERSION,
            }
        # Fill asset_class from the modal (most-common) non-null class
        # observed for that fund.
        if not positions_df.empty:
            ac = (
                positions_df.dropna(subset=["asset_class"])
                .groupby("fund_id")["asset_class"]
                .agg(lambda s: s.mode().iat[0] if not s.mode().empty else None)
                .to_dict()
            )
            for fid, cls in ac.items():
                if fid in fund_rows:
                    fund_rows[fid]["asset_class"] = cls
        funds_df = pd.DataFrame(list(fund_rows.values()), columns=FUND_COLUMNS)
    else:
        funds_df = pd.DataFrame(columns=FUND_COLUMNS)

    # --- Bucket B: overlay static reference CSV onto funds_df ---
    # Reference CSV is keyed by fund_id and carries `fund_code`,
    # `code_source`, `code_confidence`. We only fill rows where Tier 1/A
    # didn't resolve a code; existing codes (extracted from the source
    # workbook itself) take precedence over the reference table.
    ref_path = (
        Path(fund_codes_reference) if fund_codes_reference else DEFAULT_FUND_CODES_REFERENCE
    )
    if not funds_df.empty and ref_path.exists():
        ref = pd.read_csv(ref_path, dtype=str)
        ref_map = (
            ref.set_index("fund_id")[["fund_code", "code_source", "code_confidence"]]
            .to_dict(orient="index")
        )
        for i, frow in funds_df.iterrows():
            if pd.notna(frow["fund_code"]):
                continue
            ref_row = ref_map.get(frow["fund_id"])
            if ref_row is None:
                continue
            funds_df.at[i, "fund_code"] = ref_row["fund_code"]
            funds_df.at[i, "code_source"] = ref_row["code_source"]
            funds_df.at[i, "code_confidence"] = ref_row["code_confidence"]

    # Backfill fund_code + code_confidence on events, positions, lots,
    # observations via fund_id → (fund_code, code_confidence) lookup off
    # funds_df. Existing fund_code values (from Tier 1/A early-stamp) are
    # preserved.
    def _backfill_codes(df: pd.DataFrame, funds_df: pd.DataFrame) -> pd.DataFrame:
        if df.empty or funds_df.empty:
            return df
        code_map = dict(zip(funds_df["fund_id"], funds_df["fund_code"], strict=False))
        conf_map = dict(zip(funds_df["fund_id"], funds_df["code_confidence"], strict=False))
        existing_code = df.get("fund_code") if "fund_code" in df.columns else None
        if existing_code is not None:
            df["fund_code"] = existing_code.where(
                existing_code.notna(), df["fund_id"].map(code_map)
            )
        else:
            df["fund_code"] = df["fund_id"].map(code_map)
        df["code_confidence"] = df["fund_id"].map(conf_map)
        return df

    events_df = _backfill_codes(events_df, funds_df)
    positions_df = _backfill_codes(positions_df, funds_df)
    lots_df = _backfill_codes(lots_df, funds_df)

    # Fund-alias table — every source string we saw, with the
    # canonical fund_id it resolved to. Prism's identity registry can
    # absorb this directly at adapter time. The resolver only populates
    # Tier 1 codes; overlay Bucket A + B from funds_df for full coverage.
    alias_rows = resolver_to_dataframe_rows(resolver)
    fund_aliases_df = (
        pd.DataFrame(alias_rows)
        if alias_rows
        else pd.DataFrame(
            columns=["fund_id", "fund_code", "canonical_name", "alias", "is_canonical"]
        )
    )
    if not fund_aliases_df.empty and not funds_df.empty:
        code_map = dict(zip(funds_df["fund_id"], funds_df["fund_code"], strict=False))
        conf_map = dict(zip(funds_df["fund_id"], funds_df["code_confidence"], strict=False))
        fund_aliases_df["fund_code"] = fund_aliases_df["fund_code"].where(
            fund_aliases_df["fund_code"].notna(),
            fund_aliases_df["fund_id"].map(code_map),
        )
        fund_aliases_df["code_confidence"] = fund_aliases_df["fund_id"].map(conf_map)
    fund_aliases_df["source_id"] = SOURCE_ID
    fund_aliases_df["schema_version"] = SCHEMA_VERSION

    # Write outputs.
    events_df.to_parquet(out_dir / "events.parquet", index=False)
    positions_df.to_parquet(out_dir / "positions.parquet", index=False)
    lots_df.to_parquet(out_dir / "lots.parquet", index=False)
    funds_df.to_parquet(out_dir / "funds.parquet", index=False)
    fund_aliases_df.to_parquet(out_dir / "fund_aliases.parquet", index=False)

    # Observations table (balance-style — cumulative DRIP, etc.).
    # Dedupe by (fund_id, observation_type, value) keeping earliest as_of.
    if observations:
        obs_df = (
            pd.DataFrame(observations)
            .sort_values("as_of")
            .drop_duplicates(subset=["fund_id", "observation_type", "value"], keep="first")
            .reset_index(drop=True)
        )
        # Backfill fund_code + code_confidence via fund_id lookup off funds_df.
        obs_df = _backfill_codes(obs_df, funds_df)
        # Reindex to canonical OBSERVATION_COLUMNS order, filling NaN for any
        # missing columns (forward-compatible with future shape additions).
        obs_df = obs_df.reindex(columns=OBSERVATION_COLUMNS)
    else:
        obs_df = pd.DataFrame(columns=OBSERVATION_COLUMNS)
    obs_df.to_parquet(out_dir / "observations.parquet", index=False)
    if not asset_summary_df.empty:
        # tuple column doesn't pickle to parquet cleanly — convert to str
        if "target_weight_range" in asset_summary_df.columns:
            asset_summary_df["target_weight_range"] = asset_summary_df["target_weight_range"].apply(
                lambda r: f"{r[0]:.2f}-{r[1]:.2f}" if isinstance(r, tuple) else None
            )
        asset_summary_df["source_id"] = SOURCE_ID
        asset_summary_df["schema_version"] = SCHEMA_VERSION
        asset_summary_df.to_parquet(out_dir / "asset_class_summary.parquet", index=False)

    manifest = {
        "workbook": str(workbook_path),
        "artifact_hash": artifact_hash,
        "extractor_version": EXTRACTOR_VERSION,
        "extracted_at": ctx.extracted_at.isoformat(),
        "sheet_classification": sheet_classification,
        "events_path": events_path,
        "events_log_count": len(events_log_events),
        "counts": {
            "events": len(events_df),
            "events_by_type": (
                events_df["event_type"].value_counts().to_dict() if not events_df.empty else {}
            ),
            "positions": len(positions_df),
            "lots": len(lots_df),
            "funds": len(funds_df),
            "asset_class_summary_rows": len(asset_summary_df),
            "observations": len(obs_df),
            "fund_aliases": len(fund_aliases_df),
            "issues": len(issues),
        },
    }
    (out_dir / "extraction_manifest.json").write_text(json.dumps(manifest, indent=2, default=str))
    (out_dir / "issues.json").write_text(json.dumps(issues, indent=2, default=str))

    return {
        "events": events_df,
        "positions": positions_df,
        "lots": lots_df,
        "funds": funds_df,
        "asset_class_summary": asset_summary_df,
        "annualized": annualized_result,
        "issues": issues,
        "manifest": manifest,
    }
