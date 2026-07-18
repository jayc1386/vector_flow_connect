# Changelog

All notable changes to this project will be documented in this file.

## [0.16.1] — 2026-07-18

### Fixed

- **`alpaca.news` rate-limit resilience** — the news client's retry
  budget is raised to 20 attempts x 5s (alpaca-py's 3 x 3s default
  cannot outlast a per-minute rate-limit window reset, so multi-hour
  backfill drains died mid-run on a plain 429). Set via the private
  `RESTClient` attributes because `NewsClient.__init__` exposes no
  retry params; breaks loudly if an alpaca-py upgrade renames them.

## [0.16.0] — 2026-07-18

Alpaca news connector (`/v1beta1/news`, Benzinga-sourced).

### Added

- **`alpaca.news`** — `AlpacaNewsFetcher` + frozen `FetchedNewsArticle`
  model + `NewsFetcher` Protocol. Historical drain over `[start, end]`,
  oldest-first, always `limit=None` (the endpoint pages at 50 items and
  a non-None limit silently truncates the drain — same trap as
  corporate actions, fixed there in v0.10.0). Optional symbol filter is
  chunked at 100 symbols/request (URL-length safety) with id-level
  dedup across chunks; the article's full `symbols` list is preserved
  regardless of the filter, so per-article symbol counts stay honest.
  `include_content` opt-in (HTML body; empty normalizes to `None`).
  Env-proxy hygiene via the shared `_session.disable_env_proxies`
  (v0.14.2). Note for point-in-time consumers: the vendor documents no
  immutability guarantee for `created_at`; carry `updated_at` alongside
  and treat `updated_at > created_at` as a revision marker.

## [0.15.0] — 2026-07-15

Collision-aware snapshot `as_of` resolution for `dku.master_record`.
Two `YYYYMMDD` snapshot sheets could resolve to the same valuation
date when a stale title cell named another sheet's date (e.g. tab
`20210208` with a title cell reading 截止02月18日 collides with the
real `20210218` sheet). Downstream per-`(as_of, security)` aggregation
then summed the two sheets, doubling that date's positions.

### Changed

- **`snapshot.resolve_as_of(sheet_dates)`** — new pure resolver run by
  `workbook.extract` across ALL snapshot sheets before parsing. Prefers
  the title-cell date (the workbook's stated valuation date), but when
  >1 sheet resolves to the same date the tab-native sheet keeps it and
  the others fall back to their own tab date (they are distinct real
  snapshots — one with a wrong title cell). `parse_sheet` gains an
  `as_of_override` param; in workbook mode it trusts the resolver's
  verdict, so title/tab date-issue emission moves to the resolver.

### Added

- **`as_of_collision` issue** — emitted whenever two sheets contend for
  one date (the case that doubles positions). `title_date_mismatch`
  (benign relabel) is unchanged. Both surface in `issues.json`.

## [0.14.2] — 2026-07-07

Vendor HTTP ignores env proxies (prism proxy-leak plan; qh relay
0067). Server-global `HTTPS_PROXY` (the operator's Anthropic tunnel)
leaks into tmux-launched workers; vendor market-data/registry traffic
must go direct.

### Changed

- **Alpaca fetchers** (`bars` / `options` / `positions` /
  `corp_actions` incl. the announcements-sidecar `TradingClient`):
  every alpaca-py client's `requests.Session` is flipped to
  `trust_env=False` at construction via the new
  `alpaca._session.disable_env_proxies` helper. alpaca-py (>=0.31)
  offers no injection point, so this reaches into the private
  `_session` attribute — deliberately unguarded so an SDK upgrade
  that renames it fails loudly.
- **`PolygonRestClient`**: default-constructed `httpx.Client` gains
  `trust_env=False`. Injected `http_client=` (tests) untouched.
- **`AMACClient`**: same `trust_env=False` (China registry must never
  ride the tunnel).
- Untouched by design: `manager_reports` Anthropic SDK usage (the
  tunnel exists FOR Anthropic traffic) and the AMAC Playwright
  `BrowserClient` (different mechanism — Chromium launch proxy args;
  env-inheritance unverified, known follow-up).

## [0.14.1] — 2026-06-11

`dku.action_log` alignment with ACTION_LOG_SPEC 2026-06-11b (prism
plan 0063).

### Changed

- **`Pool` is free text** (was `Literal["留本", "非留本"]`): the
  资金池 vocabulary is PROVISIONAL — DKU's real partition is ≥3
  buckets (留本 / 专户 / pure cash-management) and the naming ruling
  is pending. Never an enum.
- **Pool tags accepted on any action**: the `pool_unexpected` lint is
  retired — spec 2026-06-11b sets pool on internal events funded by a
  non-default pool (the three 专户 BUYs carry pool=非留本).
  Misattribution surfaces downstream in the pool-aware cash walk.
- **Amount-only BUYs are first-class** (`buy_unpriced`, info): the
  专户 cost-only pre-statement shape (quantity/nav blank pending the
  fund's first statement) no longer trips `missing_required_field`.
- **Loader tolerates `needs_dku_confirm`**: dkup's review column ships
  on the real artifact; sanctioned but not contract — dropped on load.
  All other header drift still raises.

## [0.14.0] — 2026-06-11

New `vector_flow_connect.dku.returns_calc` subpackage for prism plan
0062 (DKU 可投-level analytics).

### Added

- **`dku.returns_calc`**: extractor for the 追溯调整至2022 ledger
  shared by DKU's 收益计算 workbooks (04 留本 / 05 可投, `scope`
  param) — DKU's own unitization series (总资产市值 / 总份额 / 净值)
  plus per-row `row_kind` classification (predicates ported from dkup
  `backfill_action_log.py`) and precomputed `units_delta` for
  mint-event detection. Emits `ledger_series_{scope}.parquet` +
  per-scope manifest/issues. Only the ledger sheet is opened (broken
  sibling sheets can't reach the parser). Deposit sheets deliberately
  not parsed in v1 (cash-sleeve recipe descoped — 现金产品 留本/非留本
  attribution is manual per DKU).

## [0.13.0] — 2026-06-11

Namespace restructure (prism plan 0061). Pure relocation plus the
share_class_expectations port.

### Changed

- `master_record` + `action_log` → `vector_flow_connect.dku.*`;
  `pdf` → `manager_reports` (China-allocator-generic capability;
  "pdf" named the input format). Placement rule codified in README +
  `dku/__init__.py`.
- Shared dkup-canonical column lists + ID hashers hoisted to
  top-level `extraction_contract.py`; `dku.master_record.canonical`
  re-exports unchanged.

### Added

- `dku.master_record.reconcile.load_expected_share_class_divergence_funds`
  (explicit-path loader; client CSV never committed here) +
  `share_class_divergence_fund_ids` in `apply_data_quality_flags`;
  `share_class_net_vs_gross_nav` joins the flag enum at rank 1
  (annotates, never masks).

## [0.12.0] — 2026-06-10

New `vector_flow_connect.action_log` subpackage for prism plan 0059
(DKU action-log ingest + lot-based analytics), per the binding
prism-vfc split-rule. Contract: dkup `ACTION_LOG_SPEC.md` (relay
green-light 2026-06-10T13:15Z).

### Added

- **`action_log.canonical`**: frozen `ActionLogEvent` Pydantic model
  (13-column CSV schema verbatim; 9-action vocabulary incl.
  `PERF_FEE`; `pool` Literal 留本/非留本; content-hash `event_id`
  pattern `E[0-9a-f]{10}`), `RowFinding`, `SOURCE_ID =
  "dku_action_log_v1"`, `SCHEMA_VERSION = "dku-action-log-v1"`.
  `needs_dku_confirm` deliberately not modeled (temporary dkup review
  column, not in the spec).
- **`action_log.loader`**: `load_action_log(path)` — utf-8-sig,
  CRLF-tolerant, hard-fail (`ActionLogSchemaError`) on header drift /
  non-numeric decimals / model-shape violations; blank→None coercion;
  file order preserved.
- **`action_log.validate`**: `validate_events(events)` — pure
  row-level checks: R-V2 arithmetic with nav-quantization-aware
  tolerance `max(rv2_abs_tol, |qty| * half_ulp(nav))` (real SELL
  rows sit ¥1-17 off at a ¥22 bound from 4dp nav rounding);
  quantity-only cumulative DRIP = first-class info finding
  (`drip_quantity_only`); duplicate event_ids; per-action
  required-field shape; pool placement; CASH-row action sanity;
  non-CNY warning; GUESSED/tentative/CONFLICT note markers →
  `pending_dku_confirmation`. R-V1/R-V4 are replay-level
  (prism-side); R-V5 deferred (needs prior-ingest state).

## [0.7.0] — 2026-05-23

Canonical-emit polish + prism plan 0040 typed-tool seam prep. Lifts
the dkup-side polish (commits `f1ff736` → `8020524`) and the
`record_cash_flow` / `record_data_quality_finding` contract additions
prism asked for on the relay (2026-05-23T03:29Z).

### Added

- **`fund_code` row-level coverage**: every event / lot / position /
  observation row now carries `fund_code` (nullable) plus
  `code_confidence` (`confirmed | tentative`). Three-tier resolution:
  Tier 1 (parens in `source_fund_string`), Bucket A (decode
  `fnd_<6-digit>` from `fund_id`), Bucket B (static reference CSV
  passed via `extract(..., fund_codes_reference=...)`). `funds.parquet`
  additionally carries `code_source` (`CSRC | AMAC | issuer_internal`).
  Tentative codes are guesses pending tenant confirmation and MUST
  NOT be cross-tenant-merged by prism's identity registry until
  resolved.
- **`reconcile.py` TypedDicts**: `CashflowIssue`, `UnitIssue`,
  `DripGapRow`, `ReconcileResult`. The function signature now
  declares `-> ReconcileResult` so future shape drift fails loudly
  via mypy / runtime introspection. Lock for prism's
  `record_data_quality_finding` typed-tool mapping.
- **`PayoutForm` enum + `validate_payout_form()`**: third value-axis
  validator in `_inherited_canonical_contract` alongside `AssetClass`
  and `DataQualityFlag`. `PayoutForm = Literal["cash", "reinvested"]`,
  with `None` valid for non-dividend event types.
- **Synthetic workbook fixture** exercises all three
  `record_cash_flow.flow_type` discriminators (distribution / drip /
  performance_fee) + non-empty `unit_issues` (1500-unit perf_fee
  mismatch) + non-empty `drip_gap_rows` (cumulative_drip 10000 vs
  attested per-event sum 1802.45) end-to-end through the real
  master_record pipeline.

### Changed

- **`events_sheet.py` Path A payout_form fixes** (audit-and-close
  from prism's plan 0040 ask #3):
  - `分红再投` now emits `payout_form="reinvested"` (was `"drip"`).
    The new value matches Path B (notes_parser) and the canonical
    `PayoutForm` enum. Downstream prism mapping
    (`event_type=dividend + payout_form=reinvested → drip`) is
    unchanged.
  - `申购费` / `赎回费` / `业绩报酬` no longer overload
    `payout_form` with `"entry_fee"` / `"exit_fee"` /
    `"performance_fee"` strings. The canonical contract reserves
    `payout_form` for dividend events; `perf_fee` rows now carry
    `payout_form=None` and the subtype rides into `notes_raw` with a
    structured `[perf_fee_subtype={entry_fee|exit_fee|performance_fee|...}]`
    prefix.
  - `EVENT_TYPE_MAP` widened from `(event_type, payout_form_or_qualifier)`
    to `(event_type, payout_form, perf_fee_subtype)` so dimensions are
    explicit; `validate_payout_form()` guards the dict at row emit.
- **`drip_gap_rows[i].as_of` normalized to ISO date string** to match
  `unit_issues[i].as_of`. Previously inherited the underlying
  observations parquet dtype.

### Compatibility

- Pure additive on the row schemas (`fund_code`, `code_confidence`,
  `code_source`). Existing prism columns unaffected.
- `payout_form` value-space narrowed for `perf_fee` rows from
  `{entry_fee, exit_fee, performance_fee, None}` to `{None}`. Prism's
  `record_cash_flow.flow_type` mapping flattens `perf_fee →
  performance_fee` anyway, so the change is invisible at the
  typed-tool layer. Pre-v0.7.0 events.parquet rows with the old fee
  values do NOT round-trip through validation — re-extract them via
  v0.7.0 to migrate.
- TypedDict additions are purely declarative (no runtime check). Old
  callers receiving the dict shape are unaffected.

## [0.4.0] — 2026-05-21

Adds the AMAC private-fund registry scraper as a subpackage
(`vector_flow_connect.amac`), lifted from `prism/src/prism/amac/`
per the prism-vfc separation-of-concerns rule
(`PRISM_HANDOFF.md §3+§5`). No behavioural change vs the prism
v0.1.0 lift — the bug being fixed is *location*, not shape.

### Added

- `vector_flow_connect.amac` subpackage (11 modules + 10 test
  files + 2 fixtures, lifted verbatim from
  `prism/src/prism/amac/`). Mechanical `prism.amac` →
  `vector_flow_connect.amac` rewrite; `from datetime import UTC`
  rewritten to `from datetime import timezone` + `timezone.utc`
  uses for Python 3.10 floor compatibility.
- Public surface: `BrowserClient` (Playwright/Chromium detail
  fetcher), `crawl_bulk(*, headed, resume, ...)` (the full
  registry crawl entry point), `crawl_incremental(*,
  seen_fund_nos, max_pages, ...)` (resume-from-state +
  stop-marker incremental crawl), `merge_batches(batches_dir,
  index_path)` (parquet batch consolidation), `AMACClient`
  (httpx-based JSON list-page client), `search_by_name`
  (lookup-by-name helper), `AMACRecord` TypedDict + the schema
  module's `COLUMN_ORDER`, `PARQUET_SCHEMA`, `SCHEMA_VERSION`,
  `SOURCE_ID` constants.
- Runtime deps: `httpx>=0.27`, `playwright>=1.40`,
  `pyarrow>=15`. Chromium binary install required for live
  crawls — operators run `uv run playwright install chromium`
  once.

### Why

Plan 0037 (prism) lifted the AMAC scraper into
`prism/src/prism/amac/` as a root-level subpackage. On post-ship
review, this surfaced as a separation-of-concerns drift from
`PRISM_HANDOFF.md §3+§5`, which directs DKU-side scraper
graduations to vfc (the shared mechanics layer for prism +
quant_hive), not into prism directly. The binding split-rule
codified on the dkup ↔ prism relay (2026-05-21T01:40Z):

- Scraper-shape (network IO + parsing + retry + state, no
  Postgres `Connection` ever crossing the module boundary) →
  vfc.
- Adapter-shell-shape (holds the `Connection`, writes through
  canonical state, emits audit events, resolves tenant) → prism.
- Canonical schemas + Alembic migrations → prism.

The AMAC scraper is unambiguously scraper-shape: 11 files of
Playwright + httpx + parse + state + retry, with no Connection
ever crossing. Plan 0037.1 corrects the drift by relocating the
module here.

### Compatibility

- Pure additive surface. Existing `vector_flow_connect.alpaca.*`
  fetchers + Protocols + models unchanged.
- Adds `httpx`, `playwright`, `pyarrow` to runtime deps. Existing
  consumers that don't import `vector_flow_connect.amac` pay an
  install cost on `uv sync` but pay zero import cost.
- Python floor stays at `>=3.10`. The lifted module's two
  3.11-only imports (`from datetime import UTC`) were
  mechanically rewritten to 3.10-compatible `timezone.utc`
  during the lift. quant_hive's v0.3.0 pin remains valid.

## [0.3.0] — 2026-05-19

Adds `AlpacaPositionsFetcher` for the broker-of-record snapshot
prism's reconciliation engine needs as its third diff source.

### Added

- `FetchedPosition` Pydantic model in
  `vector_flow_connect.alpaca.positions` — vendor-agnostic shape for
  one open position. Fields: `symbol`, `qty` (signed Decimal),
  `side` (`'long' | 'short'`), `avg_entry_price`, `market_value`
  (nullable), `cost_basis`, `unrealized_pl` (nullable), `asset_class`.
  Frozen + `extra='forbid'` per package convention.
- `PositionsFetcher` Protocol in `vector_flow_connect.alpaca._base`
  with two methods: `get_positions() -> list[FetchedPosition]` and
  `get_account_number() -> str`. Vendor-neutral contract.
- `AlpacaPositionsFetcher` in `vector_flow_connect.alpaca.positions`
  — concrete `PositionsFetcher` backed by alpaca-py's `TradingClient`.
  Constructed via `AlpacaPositionsFetcher.from_credentials(creds)`
  taking an `AlpacaTradingCredentials` (the same model v0.2.0
  introduced for the corp-actions sidecar).

### Why

prism plan 0028 (shipped 2026-05-19) landed the three-view
reconciliation engine (fills vs holdings vs broker-positions) but
deferred the live broker pull — the fixture-driven acceptance test
injects rows directly into `positions.broker_positions_raw`. Closing
that gap requires a vfc-side fetcher so the adapter shell that
populates the table follows the same shape as the bars / corp-actions
/ options paths.

`get_all_positions()` returns the whole account snapshot in one
response — no chunking, no pagination, no symbol-list parameter. The
fetcher mirrors that: one method, one round trip. `get_account_number()`
exposes the broker's stable account identifier so the consumer can
write it as the `account_id` field on bitemporal position tables
(rather than a literal placeholder).

Account-level data (cash, equity, buying_power) deferred to a future
release — prism's strict-FoF reconciliation invariant already gets
`cash` from quant_hive's emit channel, so v0.3.0 doesn't need to
duplicate.

### Compatibility

- Pure additive surface. Existing fetchers + Protocols + models
  unchanged. Adding a new Protocol class is minor-bump-safe (no
  existing consumer fakes implement `PositionsFetcher`, so no break).
- `alpaca-py>=0.31` already ships `TradingClient.get_all_positions()`
  + `get_account()`; no dep-floor bump.
- Both `paper=True` (default) and `paper=False` routes accepted.
  Same paper credentials work as for the v0.2.0 announcements sidecar.

## [0.2.0] — 2026-05-17

Adds `declared_date` (dividend announcement date) to
`FetchedCorpAction` via a sidecar call to Alpaca's deprecated
trading-API announcements endpoint.

### Added

- `AlpacaTradingCredentials` in `vector_flow_connect.alpaca.settings`.
  Optional augmentation for corp-actions fetchers. Distinct shape from
  `AlpacaCredentials` because Alpaca's trading-API uses different keys
  than its market-data API.
- `FetchedCorpAction.declared_date: date | None = None` — populated
  from the announcements sidecar when trading credentials are
  provided; left None on every event otherwise.
- `AlpacaCorpActionsFetcher.from_credentials(credentials,
  trading_credentials=...)` keyword. When omitted, behaviour is
  identical to v0.1.x.

### Why

Alpaca's market-data `/v1/corporate-actions` endpoint does NOT carry
`declaration_date` on cash_dividend events (verified empirically
2026-05-17 across 5 large-cap dividend payers). The only Alpaca
surface that exposes the field is the trading-API
`/v2/corporate_actions/announcements` endpoint, which alpaca-py
marks as deprecated (redirecting users to the market-data endpoint
that lacks the field — an unresolved Alpaca regression). This release
accepts the deprecation risk to capture declaration_date while
available; historical values written to canonical storage remain
valid regardless of future endpoint changes.

The sidecar pulls universe-wide announcements per ≤90-day chunk
(the endpoint's documented cap), builds a
`(initiating_symbol, ex_date) -> declaration_date` lookup, and
attaches during normalisation. Universe-wide queries are cheaper
than per-symbol fanout for typical multi-ticker ingest patterns;
the data volume is small (~10k events/quarter).

### Compatibility

- Pure additive change to `FetchedCorpAction`; consumers that
  constructed it positionally must verify their kwargs. The model
  uses Pydantic kwarg-only construction so existing consumers should
  be fine.
- Consumers that don't pass `trading_credentials=` see identical
  behaviour to v0.1.x (declared_date stays None on every event).
- Fixes the stale `__version__ = "0.1.0"` in
  `vector_flow_connect/__init__.py` — was out of sync with v0.1.1
  in `pyproject.toml`.

## [0.1.1] — 2026-05-15

Python-floor loosening for quant_hive compatibility.

### Changed

- `requires-python` lowered from `>=3.13` to `>=3.10`. The v0.1.0 floor
  was inherited from prism's `pyproject.toml` without auditing actual
  syntactic needs; the connector code uses only PEP 585 builtin
  generics + `from __future__ import annotations` + `Protocol` +
  `Literal` + `Any`, all valid on 3.10. No `Self` (3.11+),
  `LiteralString` (3.11+), PEP 695 type-param syntax (3.12+),
  `match`/`case` (3.10+ but unused), or `except*` (3.11+).
- `tool.ruff.target-version` → `py310`.
- `tool.pyright.pythonVersion` → `3.10`.
- CI workflow tests against Python 3.10 to validate the floor.

### Why

quant_hive's `pyproject.toml` is `requires-python = ">=3.10"` (running
3.10.12 in production). vectorbt + adjacent deps make bumping qh's
Python non-trivial, so the lower-Python-bound is being met on the
connectors side instead.

## [0.1.0] — 2026-05-15

Initial release. Lifts vendor-API mechanics out of prism's adapters
into a system-agnostic shared package consumed by both prism and
quant_hive.

### Added

- `vector_flow_connect.alpaca.bars` — `AlpacaBarFetcher` + `FetchedBar`
  Pydantic model.
- `vector_flow_connect.alpaca.options` — `AlpacaOptionsFetcher` +
  `FetchedOptionContract` + `FetchedOptionBar` Pydantic models +
  `fetch_chain_bars` high-level primitive (OCC enumeration internal,
  storage-agnostic via `spot_lookup` callable).
- `vector_flow_connect.alpaca.corp_actions` —
  `AlpacaCorpActionsFetcher` + `FetchedCorpAction` Pydantic model;
  v1 event types `cash_dividend` / `forward_split` / `reverse_split`.
- `vector_flow_connect.alpaca.occ` — `parse_occ_symbol`,
  `generate_occ_symbol`, `friday_expirations`, `strikes_in_band`
  helpers. Decimal-quantize fix from prism Plan 0023 (ROUND_FLOOR /
  ROUND_CEILING strike boundaries).
- `vector_flow_connect.alpaca._base` — `BarFetcher`, `OptionsFetcher`,
  `CorpActionsFetcher` Protocols.
- `vector_flow_connect.alpaca.settings` — `AlpacaCredentials`
  Pydantic model (vendor-neutral; consumers map their local settings
  in).

### Deferred to v0.2.0+

- Live-Alpaca smoke testing in CI (v0 is stubbed-only against
  monkeypatched alpaca-py clients).
- Retry / rate-limit / backoff policy abstractions (v0 wraps the SDK
  at face value; throttle is a fetcher class attribute).
- `vendors/alpaca/` directory restructure (v0 layout is flat;
  promotes when a 2nd vendor lands).
- `YFinanceFetcher` (quant_hive research-only; future vendor).
- `AlpacaAssetFetcher` (quant_hive trading-API; stays in qh).
- News / polygon / IEX adapters (separate plans).
- PyPI publishing (v0 is SHA-pinned git installs only).
