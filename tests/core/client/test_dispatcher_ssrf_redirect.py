"""Redirect-hop SSRF validation in the httpx dispatcher.

The request pipeline validates the URL the caller supplied, but httpx follows
redirects internally — so a public host could bounce the client to an internal
address that never passed through that check. The dispatcher installs a request
event hook (httpx invokes it once per hop) that re-runs the guard on every URL
the transport is about to touch.
"""

from __future__ import annotations

import httpx
import pytest

from equinox.core.client.cookie_handler import CookieHandler
from equinox.core.client.dispatcher import HttpxDispatcher
from equinox.core.exceptions import ValidationError


def _dispatcher() -> HttpxDispatcher:
    return HttpxDispatcher(
        timeout=5.0,
        follow_redirects=True,
        verify_ssl=True,
        proxy=None,
        cookie_handler=CookieHandler(None),
    )


def test_client_registers_the_ssrf_request_hook():
    """The shared client must actually carry the per-hop guard."""
    client = _dispatcher()._ensure_client()
    hooks = client._event_hooks.get("request", [])
    assert any(
        getattr(hook, "__func__", hook) is HttpxDispatcher._validate_outbound_request
        for hook in hooks
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://metadata.google.internal/",
        "http://10.0.0.5/",
        "http://127.0.0.1:8080/",
        "http://[::1]/",
    ],
)
def test_hook_blocks_internal_redirect_targets(url):
    """Every hop is validated, so a redirect to an internal address is refused."""
    with pytest.raises(ValidationError):
        HttpxDispatcher._validate_outbound_request(httpx.Request("GET", url))


def test_hook_allows_a_public_literal_address():
    """A literal public IP needs no DNS, keeping this test hermetic."""
    HttpxDispatcher._validate_outbound_request(httpx.Request("GET", "http://93.184.216.34/"))
