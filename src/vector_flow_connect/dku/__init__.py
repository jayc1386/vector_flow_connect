"""Client-artifact parsers for DKU (Duke Kunshan endowment).

Placement rule (vfc package layout): a top-level subpackage is a
generic capability against a vendor API or a public/industry-standard
artifact class (`alpaca`, `polygon`, `amac`, `manager_reports`); a
`<client>/` subpackage parses that client's OWN operational artifacts.
A module earns top level only once client specifics are parameterized
away (the `fund_codes_reference`-style pattern — reference data passed
in by path, never committed here).

- `dku.master_record` — 留本基金动态资产配置情况.xlsx workbook
  extractor (the marks side: snapshots, lots, positions, funds).
- `dku.action_log` — 交易流水台账 CSV loader + row validations (the
  authoritative events side).
"""
