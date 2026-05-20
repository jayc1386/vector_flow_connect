"""Offline tests for AMACClient request shape using httpx MockTransport."""

from __future__ import annotations

import httpx

from vector_flow_connect.amac._selectors import FUND_LIST_ENDPOINT
from vector_flow_connect.amac.client import AMACClient


def _make_client_with_transport(handler) -> AMACClient:
    """Construct an AMACClient backed by an httpx MockTransport."""
    transport = httpx.MockTransport(handler)
    client = AMACClient(sleep_between_requests=0)
    client._http = httpx.Client(transport=transport)
    return client


def test_search_without_sort_sends_no_sort_param():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.content.decode()
        return httpx.Response(
            200,
            json={
                "content": [],
                "totalElements": 0,
                "totalPages": 0,
                "size": 20,
                "number": 0,
                "first": True,
                "last": True,
            },
        )

    with _make_client_with_transport(handler) as c:
        c.search(page=0, size=20)

    assert "sort=" not in captured["url"]
    assert FUND_LIST_ENDPOINT in captured["url"]
    assert "page=0" in captured["url"]
    assert "size=20" in captured["url"]


def test_search_with_sort_includes_sort_in_url():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(
            200,
            json={
                "content": [],
                "totalElements": 0,
                "totalPages": 0,
                "size": 100,
                "number": 0,
                "first": True,
                "last": True,
            },
        )

    with _make_client_with_transport(handler) as c:
        c.search(page=0, size=100, sort="putOnRecordDate,desc")

    assert "sort=putOnRecordDate" in captured["url"]
    # httpx URL-encodes the comma; either form is acceptable
    assert "desc" in captured["url"]


def test_search_with_keyword_sends_body():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content.decode()
        return httpx.Response(
            200,
            json={
                "content": [],
                "totalElements": 0,
                "totalPages": 0,
                "size": 20,
                "number": 0,
                "first": True,
                "last": True,
            },
        )

    with _make_client_with_transport(handler) as c:
        c.search(keyword="睿见1号")

    assert "睿见1号" in captured["body"] or "keyword" in captured["body"]
