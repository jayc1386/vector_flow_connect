# Changelog

All notable changes to this project will be documented in this file.

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
