# vector-flow-connect — Claude session notes

Shared vendor-API connectors for `prism` (portfolio analytics) and
`quant_hive` (research + trading). Lifted from prism's adapters in
prism plan 0024 (2026-05-15).

## Load-bearing invariant: shape coercion, never storage

The whole reason this repo exists is to be shape-only — both consumers
own different canonicalisation layers (prism: bitemporal raw/golden +
typed-tool seam; qh: source-list + DuckDB cache). If you find yourself
reaching for any of these, the design has drifted and the change
belongs in the consumer:

- `import sqlalchemy` / `import duckdb` / `import psycopg`
- `from prism.config import ...` / `from quant_hive.config import ...`
- Reading env vars (`os.environ`, `pydantic-settings`, `.env`)
- File I/O for credentials, caches, or any persistent state
- Database queries, even read-only

Storage-dependent inputs come in via injection: credentials via
`AlpacaCredentials`, spot prices via `spot_lookup: Callable[[date],
Decimal | None]`. That callable pattern is the precedent for any
future "this primitive needs data the storage layer has" case.

## What the consumers look like

- **prism** (`~/projects/code/prism`) — pins via
  `[tool.uv.sources]` SHA in `pyproject.toml`. Python 3.13. Uses
  `vector_flow_connect.alpaca.*` to power adapter shells under
  `src/prism/adapters/alpaca_*.py`. Adapter shell owns smart-delta,
  typed-tool emission, audit events, security-id resolution,
  year-chunking.
- **quant_hive** (`~/projects/code/quant_hive`) — same pinning pattern.
  Python 3.10.12 (vectorbt + adjacent deps block a bump; that's why
  the connectors floor is 3.10 not 3.13). Uses the package for both
  ingest paths AND direct research-only access; consumer side is
  `quant_hive/data_ops/sources/alpaca_direct*.py`.

If a change feels asymmetric (good for one, awkward for the other),
it's probably not connector-shaped — push back and let the asymmetric
consumer absorb it.

## Releases

**Shipped:**

- **v0.3.0** (2026-05-19, SHA `<TBD on tag>`) — adds
  `AlpacaPositionsFetcher` + `FetchedPosition` model + `PositionsFetcher`
  Protocol. Wraps alpaca-py's `TradingClient.get_all_positions()` +
  `get_account()`. Two-method surface: snapshot all open positions,
  read the broker's stable `account_number`. Single round trip per
  call — no pagination, no chunking. Reuses v0.2.0's
  `AlpacaTradingCredentials` shape (same paper-tier key works for
  both data + trading endpoints with `paper=True`). prism plan 0030
  consumes via a new adapter shell populating
  `positions.broker_positions_raw` on a daily 21:00 UTC schedule;
  qh consumption deferred. Account-level data (cash, equity,
  buying_power) intentionally deferred — prism gets `cash` from qh's
  inbox channel already.
- **v0.2.0** (2026-05-17, SHA `599ea7f8`) — adds
  `AlpacaTradingCredentials` + `FetchedCorpAction.declared_date` +
  a sidecar call to Alpaca's deprecated trading-API
  `/v2/corporate_actions/announcements` endpoint (the only Alpaca
  surface that still exposes the announcement date — the
  market-data corp-actions endpoint dropped it in an unresolved
  regression). `AlpacaCorpActionsFetcher.from_credentials` gains
  an optional `trading_credentials=` kwarg; absent → identical
  v0.1.x behaviour (`declared_date` stays None). Sidecar chunks at
  ≤90-day boundaries (the announcements endpoint's documented
  cap), pulls universe-wide per chunk, joins on `(initiating_symbol,
  ex_date)`. Both prism (plan 0026) and qh (commit `f94b59a`)
  pinned the SHA on the same day.
- **v0.1.1** (2026-05-15, SHA `74ad64d0`) — Python-floor loosen
  `>=3.13` → `>=3.10` for quant_hive parity. No code change.
- **v0.1.0** (2026-05-15, SHA `7a60f699`) — initial release.
  Lifted `Alpaca{Bar,Options,CorpActions}Fetcher` + Pydantic models
  + Protocols + OCC helpers + `fetch_chain_bars` primitive out of
  prism's adapters per quant_hive's `connectors_handoff.md`.

SemVer. Patch for bugfixes, minor for additive surface (new fetcher,
new method, new Protocol), major for breaking. Both consumers pin by
SHA, so:

1. **Never force-push tags.** Consumers' lockfiles silently keep the
   old ref while CHANGELOG says otherwise.
2. **Tag every release**, even pre-1.0. One CHANGELOG entry per tag.
3. **Don't atomic-merge across repos.** Tag here first, then bump
   each consumer in its own PR. The "tag + report SHA" / "consumer
   pin-bump" cadence is the template — see v0.1.1 commit history.
4. **Lockstep policy**: a real-bug-fix release should bump both
   consumers immediately. Cosmetic-only bumps can lag.
5. **Breaking change choreography**: connectors release → prism PR
   → qh PR, in that order. Don't expect simultaneous merges.

## Public API surface = `src/vector_flow_connect/alpaca/__init__.py`'s `__all__`

That list is the contract. Treat anything underscore-prefixed as
internal. If a consumer is importing private names, either lift them
to public (minor bump) or refactor to give them what they actually
need.

A new method on a Protocol (`BarFetcher`, `OptionsFetcher`,
`CorpActionsFetcher`) is **breaking** — every consumer test-fake also
implements it. Minor bump with explicit CHANGELOG note.

## Python floor is 3.10 and load-bearing

CI tests on 3.10 specifically to catch slip-ups. Things that
silently break on 3.10:

- `from datetime import UTC` — 3.11+. Use `from datetime import timezone`
  + `timezone.utc`. (We caught one of these in the v0.1.0 → v0.1.1 audit.)
- `Self` from `typing` — 3.11+. Use `typing_extensions.Self` or
  forward-stringify.
- `LiteralString`, `assert_never`, `tomllib`, `except*` — 3.11+.
- `type X = ...` / `class Foo[T]:` PEP 695 syntax — 3.12+.

Builtin generics (`list[X]`, `dict[K, V]`, `X | Y` in annotations)
are fine because `from __future__ import annotations` is on every
module — that string-defers annotations, which is what makes them
work on 3.10 without runtime evaluation.

Keep `target-version` in `[tool.ruff]`, `pythonVersion` in
`[tool.pyright]`, the GitHub Actions `uv python install` step, and
`requires-python` in `[project]` all aligned. Bump them in one commit
when the floor moves.

## Testing patterns

Stubbed-only. Each fetcher's tests should cover:

- Pydantic model output shape (especially `extra="forbid"` round-trip).
- Pagination correctness (multi-symbol → flattened).
- Dedup-within-response.
- Throttle behaviour (`rate_limit_sleep_secs=0.0` for fast tests).
- Error path (fetcher raises → primitive captures, doesn't propagate).

Monkeypatch the SDK client (`fetcher._client = FakeClient(...)`)
after construction — alpaca-py's clients don't make network calls in
`__init__`, just store creds.

The OCC Decimal-quantize boundary test (`test_occ.py::test_boundary_outward_expansion`)
is load-bearing — `Decimal.__floordiv__` truncates toward zero rather
than floor toward negative infinity, which silently misses one strike
at the upper boundary. Same pattern for any future "subtle math" case:
write a regression-targeted test before fixing.

Live smoke ran ad-hoc against paper Alpaca during the v0.2.0
declared_date sidecar verification (AAPL 2024 quarterly dividends
4/4 populated; SPY 2024-12-20 → `declared_date=2024-12-18`). A
gated `VFC_LIVE_ALPACA=1` nightly smoke is still deferred — would
be useful for catching upstream-shape drift on the deprecated
announcements endpoint, where alpaca-py's redirect-to-the-other-
endpoint deprecation warning hints at eventual removal.

## Don't add abstractions ahead of demand

Brief decision #7 (locked) was "wholesale lift, no speculative
refactors." Applies going forward too:

- **No `RetryPolicy`, `RateLimitStrategy`, `TransportAdapter`
  interfaces** until ≥2 concrete implementations exist that would
  actually use them.
- **Class-attribute defaults** (`rate_limit_sleep_secs`, batch sizes)
  are fine. A vendor that needs different defaults sets them on its
  own fetcher class. That's not premature abstraction.
- **No "feature flags."** Consumers pin SHAs — switching versions IS
  the rollout mechanism.

## When a second vendor lands (v0.2+)

Currently flat under `alpaca/`. Restructure plan (from brief decision
#6 + plan 0024 DEFERRED):

1. **First** move `alpaca/` → `vendors/alpaca/`. Add back-compat
   re-exports at the old import path. Minor bump with CHANGELOG
   migration note.
2. **Then** lift Protocols `BarFetcher` / `OptionsFetcher` /
   `CorpActionsFetcher` from `alpaca/_base.py` up to `_base.py` at
   the package root — they describe vendor-agnostic contracts.
3. **Only then** add the new vendor (polygon, IEX, news, etc.) at
   `vendors/<name>/`, mirroring the alpaca module shape.

Doing this in the wrong order means restructuring twice.

## Repo hygiene

- Pre-commit ruff version and `uv`-installed ruff version must
  match. Currently both at 0.15.12. Bump them in one commit.
- `uv.lock` committed.
- `py.typed` marker — DEFERRED but cheap. Add when consumer-side
  pyright noise becomes annoying.
- PyPI publishing — not v0. Stay on git+SHA installs.

## Standard dev commands

```
uv sync --all-extras --dev   # bootstrap / refresh
uv run pytest                # 47 tests, ~1.5s
uv run ruff check src tests
uv run ruff format --check src tests
uv run pyright
```

## Pointers

- Lift origin: prism plan 0024
  (`~/projects/code/prism/docs/plans/0024-vector-flow-connect.md`).
- QH brief that triggered the lift:
  `~/projects/code/quant_hive/docs/specs/connectors_handoff.md`
  (`ac8a177`).
- v0 deferrals: see `CHANGELOG.md` and prism's `DEFERRED.md`
  "vector-flow-connect follow-ups" section.
