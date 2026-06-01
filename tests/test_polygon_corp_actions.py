"""Tests for PolygonCorpActionsFetcher + PolygonRestClient pagination.

No network: a fake client (any object exposing `paginate`) drives the
fetcher's normalisation logic; the real `PolygonRestClient` is exercised
against `httpx.MockTransport` to cover the `next_url` apiKey-reattach drain.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import httpx

from vector_flow_connect.polygon._client import PolygonRestClient
from vector_flow_connect.polygon.corp_actions import PolygonCorpActionsFetcher


class FakeClient:
    """Returns canned `results[]` rows per path; records calls."""

    def __init__(self, rows_by_path: dict[str, list[dict[str, Any]]]) -> None:
        self._rows = rows_by_path
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def paginate(self, path: str, params: dict[str, Any]):
        self.calls.append((path, dict(params)))
        yield from self._rows.get(path, [])


def _fetcher(rows: dict[str, list[dict[str, Any]]]) -> PolygonCorpActionsFetcher:
    return PolygonCorpActionsFetcher(client=FakeClient(rows))


def test_dividend_normalization_with_declaration_date() -> None:
    rows = {
        "/v3/reference/dividends": [
            {
                "ticker": "AAPL",
                "ex_dividend_date": "2026-05-11",
                "declaration_date": "2026-04-30",
                "record_date": "2026-05-11",
                "pay_date": "2026-05-14",
                "cash_amount": 0.27,
                "dividend_type": "CD",
                "id": "div-aapl-1",
            }
        ]
    }
    out = _fetcher(rows).get_corp_actions(
        symbols=["AAPL"], start=date(2026, 1, 1), end=date(2026, 12, 31)
    )
    assert len(out) == 1
    e = out[0]
    assert e.symbol == "AAPL"
    assert e.event_type == "dividend"
    assert e.ex_date == date(2026, 5, 11)
    assert e.declared_date == date(2026, 4, 30)  # the whole point: real board date
    assert e.record_date == date(2026, 5, 11)
    assert e.payable_date == date(2026, 5, 14)
    assert e.cash_amount == Decimal("0.27")
    assert e.process_date == date(2026, 5, 11)  # defaulted to ex_date
    assert e.is_special is False
    assert e.external_id == "div-aapl-1"
    assert e.cusip is None


def test_special_dividend_flag_and_missing_declaration_date() -> None:
    rows = {
        "/v3/reference/dividends": [
            {
                "ticker": "KO",
                "ex_dividend_date": "2026-03-13",
                "cash_amount": 0.51,
                "dividend_type": "SC",
            },
            {
                "ticker": "KO",
                "ex_dividend_date": "2026-06-15",
                "cash_amount": 0.51,
                "declaration_date": None,
            },
        ]
    }
    out = _fetcher(rows).get_corp_actions(
        symbols=["KO"], start=date(2026, 1, 1), end=date(2026, 12, 31)
    )
    by_ex = {e.ex_date: e for e in out}
    assert by_ex[date(2026, 3, 13)].is_special is True
    assert by_ex[date(2026, 6, 15)].declared_date is None  # null tolerated


def test_filters_to_requested_symbols() -> None:
    rows = {
        "/v3/reference/dividends": [
            {"ticker": "AAPL", "ex_dividend_date": "2026-05-11", "cash_amount": 0.27},
            {
                "ticker": "ZZZZ",
                "ex_dividend_date": "2026-05-11",
                "cash_amount": 1.0,
            },  # not requested
        ]
    }
    out = _fetcher(rows).get_corp_actions(
        symbols=["aapl"], start=date(2026, 1, 1), end=date(2026, 12, 31)
    )
    assert {e.symbol for e in out} == {"AAPL"}  # case-insensitive match; ZZZZ dropped


def test_split_normalization() -> None:
    rows = {
        "/v3/reference/splits": [
            {
                "ticker": "NVDA",
                "execution_date": "2024-06-10",
                "split_from": 1,
                "split_to": 10,
                "id": "spl-1",
            }
        ]
    }
    out = _fetcher(rows).get_corp_actions(
        symbols=["NVDA"], start=date(2024, 1, 1), end=date(2024, 12, 31)
    )
    assert len(out) == 1
    e = out[0]
    assert e.event_type == "split"
    assert e.ex_date == date(2024, 6, 10)
    assert e.split_ratio_from == Decimal("1")
    assert e.split_ratio_to == Decimal("10")
    assert e.declared_date is None  # splits carry no declaration date


def test_empty_symbols_short_circuits() -> None:
    fake = FakeClient({})
    out = PolygonCorpActionsFetcher(client=fake).get_corp_actions(
        symbols=[], start=date(2026, 1, 1), end=date(2026, 12, 31)
    )
    assert out == []
    assert fake.calls == []  # no requests issued


def test_rest_client_drains_next_url_and_reattaches_apikey() -> None:
    pages = [
        {
            "results": [{"ticker": "AAPL", "ex_dividend_date": "2026-05-11", "cash_amount": 0.27}],
            "next_url": "https://api.polygon.io/v3/reference/dividends?cursor=PAGE2",
        },
        {"results": [{"ticker": "MSFT", "ex_dividend_date": "2026-05-21", "cash_amount": 0.83}]},
    ]
    seen_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        return httpx.Response(200, json=pages[len(seen_urls) - 1])

    client = PolygonRestClient(
        api_key="SECRET",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        rate_limit_sleep_secs=0.0,
    )
    rows = list(client.paginate("/v3/reference/dividends", {"limit": 1000}))
    assert [r["ticker"] for r in rows] == ["AAPL", "MSFT"]
    assert len(seen_urls) == 2
    # apiKey present on BOTH requests (the next_url reattach gotcha).
    assert all("apiKey=SECRET" in u for u in seen_urls)
    assert "cursor=PAGE2" in seen_urls[1]
