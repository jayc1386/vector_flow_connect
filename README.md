# vector-flow-connect

Shared vendor-API connectors for [prism](https://github.com/jayc1386/prism)
and [quant_hive](https://github.com/jayc1386/quant_hive). System-agnostic
Python package — does shape coercion only, no storage opinions.

## What's here

```
src/vector_flow_connect/alpaca/
├── _base.py        # BarFetcher / OptionsFetcher / CorpActionsFetcher Protocols
├── bars.py         # AlpacaBarFetcher + FetchedBar
├── options.py      # AlpacaOptionsFetcher + fetch_chain_bars primitive
├── corp_actions.py # AlpacaCorpActionsFetcher (v0.2.0 declared_date sidecar)
├── occ.py          # OCC symbol parse/generate, Friday + strike-band enumeration
└── settings.py     # AlpacaCredentials + AlpacaTradingCredentials (vendor-neutral)
```

Layout is flat for v0. Restructures to `vendors/alpaca/` once a second
vendor (polygon, IEX) lands.

## Boundary

**In scope** — vendor SDK wrapping + shape coercion:

- NaN → 0 / None normalization.
- Dedupe at natural key within a single API response.
- OCC symbol parse + generate + Friday-expiration + strike-band
  enumeration.
- `fetch_chain_bars(...)` high-level primitive that combines OCC
  enumeration with per-symbol bar fetching, storage-agnostic via a
  `spot_lookup` callable injected by the consumer.

**Out of scope** — canonicalisation stays in the consumer:

- Provenance (source_id, recorded_at), bitemporal raw→golden,
  cross-source merging, smart-delta gates → prism's `Adapter` ABC.
- Storage adapter shells, cache layers, source-list topology →
  quant_hive's `BarSource` / `OptionsSource` Protocols.

## Install

SHA-pinned `pip install git+...`:

```
vector-flow-connect @ git+https://github.com/jayc1386/vector_flow_connect.git@<sha>
```

No PyPI for v0.

## Credentials

Vendor-neutral construction; consumers map their local settings →
`AlpacaCredentials`:

```python
from vector_flow_connect.alpaca.bars import AlpacaBarFetcher
from vector_flow_connect.alpaca.settings import AlpacaCredentials

fetcher = AlpacaBarFetcher.from_credentials(
    AlpacaCredentials(api_key="...", secret_key="...", feed="sip")
)
bars = fetcher.get_bars(symbols=["SPY"], start=date(2025, 1, 2), end=date(2025, 1, 31))
```

### `declared_date` sidecar (v0.2.0+, optional)

`AlpacaCorpActionsFetcher` optionally accepts a second credential
shape (`AlpacaTradingCredentials`) to source `declared_date` from
Alpaca's deprecated trading-API `/v2/corporate_actions/announcements`
endpoint — the only Alpaca surface that exposes the announcement
date (the market-data corp-actions endpoint dropped it in an
unresolved regression). Absent → behaviour identical to v0.1.x.

```python
from vector_flow_connect.alpaca.corp_actions import AlpacaCorpActionsFetcher
from vector_flow_connect.alpaca.settings import (
    AlpacaCredentials,
    AlpacaTradingCredentials,
)

fetcher = AlpacaCorpActionsFetcher.from_credentials(
    AlpacaCredentials(api_key="...", secret_key="..."),
    trading_credentials=AlpacaTradingCredentials(
        api_key="...",  # paper trading creds — same key value works
        secret_key="...",
        paper=True,
    ),
)
events = fetcher.get_corp_actions(
    symbols=["AAPL"], start=date(2025, 2, 1), end=date(2025, 3, 1)
)
# events[0].declared_date is populated
```

Note: Alpaca's PK-prefixed paper keys work for both the market-data
and the trading-announcements endpoints — same key value can be
passed to both credential shapes. The announcements endpoint is
deprecated; consumers should treat declared_date as best-effort and
keep the column nullable in canonical storage.

## Development

```
uv sync
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run pyright
```

CI runs all four on every push + PR.
