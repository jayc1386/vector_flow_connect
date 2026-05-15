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
├── corp_actions.py # AlpacaCorpActionsFetcher
├── occ.py          # OCC symbol parse/generate, Friday + strike-band enumeration
└── settings.py     # AlpacaCredentials (vendor-neutral)
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

## Development

```
uv sync
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run pyright
```

CI runs all four on every push + PR.
