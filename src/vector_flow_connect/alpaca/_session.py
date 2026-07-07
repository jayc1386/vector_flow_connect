"""Env-proxy hygiene for alpaca-py clients (v0.14.2).

Vendor market-data traffic must go direct — never through the
operator's ``HTTPS_PROXY`` tunnel (privoxy → Tokyo, which exists for
Anthropic traffic). Server-global proxy env vars leak into
tmux-launched workers (qh relay 0067, 2026-07-07: one tunnel blip at
T-3min killed a qh trading session), so every alpaca-py client gets
its ``requests.Session`` flipped to ``trust_env=False`` at
construction.

alpaca-py's ``RESTClient`` (>=0.31) offers no session/proxy injection
point — it always builds its own ``requests.Session`` in
``alpaca/common/rest.py`` and stores it privately as ``_session``.
The direct attribute access below is deliberate: if an alpaca-py
upgrade renames the attribute, client construction fails loudly
instead of silently re-enabling env proxies.
"""

from __future__ import annotations

from typing import Any


def disable_env_proxies(client: Any) -> Any:
    """Make an alpaca-py client ignore ``HTTP(S)_PROXY`` env vars."""
    client._session.trust_env = False
    return client
