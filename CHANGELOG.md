# Changelog

All notable changes to this project will be documented in this file.

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
