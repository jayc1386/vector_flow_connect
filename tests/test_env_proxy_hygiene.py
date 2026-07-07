"""v0.14.2 — vendor HTTP clients must ignore env proxy vars.

Server-global ``HTTPS_PROXY`` (the operator's Anthropic tunnel) leaks
into tmux-launched workers (qh relay 0067); vendor market-data and
registry traffic must go direct regardless. Alpaca clients are
asserted via the ``requests.Session.trust_env`` flag; httpx-based
clients are constructed under a poisoned proxy env and asserted to
mount no proxy transports.
"""

from __future__ import annotations

import httpx
import pytest

from vector_flow_connect.alpaca.bars import AlpacaBarFetcher
from vector_flow_connect.alpaca.corp_actions import AlpacaCorpActionsFetcher
from vector_flow_connect.alpaca.options import AlpacaOptionsFetcher
from vector_flow_connect.alpaca.positions import AlpacaPositionsFetcher
from vector_flow_connect.amac.client import AMACClient
from vector_flow_connect.polygon._client import PolygonRestClient

_POISON = "http://127.0.0.1:9"


@pytest.fixture(autouse=True)
def _poison_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.setenv(var, _POISON)


def test_alpaca_bar_fetcher_session_ignores_env_proxies() -> None:
    fetcher = AlpacaBarFetcher(api_key="k", api_secret="s", feed="iex")
    assert fetcher._client._session.trust_env is False


def test_alpaca_options_fetcher_session_ignores_env_proxies() -> None:
    fetcher = AlpacaOptionsFetcher(api_key="k", api_secret="s")
    assert fetcher._client._session.trust_env is False


def test_alpaca_positions_fetcher_session_ignores_env_proxies() -> None:
    fetcher = AlpacaPositionsFetcher(api_key="k", api_secret="s", paper=True)
    assert fetcher._client._session.trust_env is False


def test_alpaca_corp_actions_both_sessions_ignore_env_proxies() -> None:
    fetcher = AlpacaCorpActionsFetcher(
        api_key="k",
        api_secret="s",
        trading_api_key="tk",
        trading_api_secret="ts",
    )
    assert fetcher._client._session.trust_env is False
    assert fetcher._trading_client is not None
    assert fetcher._trading_client._session.trust_env is False


def test_polygon_default_client_mounts_no_env_proxies() -> None:
    client = PolygonRestClient(api_key="k")
    assert not client._http._mounts, "httpx client mounted env-proxy transports"


def test_polygon_injected_client_untouched() -> None:
    injected = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    client = PolygonRestClient(api_key="k", http_client=injected)
    assert client._http is injected


def test_amac_client_mounts_no_env_proxies() -> None:
    client = AMACClient()
    assert not client._http._mounts, "httpx client mounted env-proxy transports"
