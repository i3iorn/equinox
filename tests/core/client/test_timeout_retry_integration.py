"""Integration: httpx timeouts must engage RetryPolicy timeout retries.

Issue #50: ``RetryPolicy.execute`` only retries ``RequestTimeoutError``, while
the dispatcher originally let raw ``httpx.TimeoutException`` propagate — so
timeout retries never engaged. The dispatcher now translates the exception
(``HttpxDispatcher._send_httpx_request``); these tests pin the end-to-end
behaviour at that seam.
"""

from typing import Any

import httpx
import pytest

from equinox.core.client.cookie_handler import CookieHandler
from equinox.core.client.dispatcher import HttpxDispatcher
from equinox.core.client.retry_policy import RetryPolicy
from equinox.core.exceptions import RequestTimeoutError
from equinox.core.request import Request


def _make_dispatcher() -> HttpxDispatcher:
    return HttpxDispatcher(
        timeout=10.0,
        follow_redirects=True,
        verify_ssl=True,
        proxy=None,
        cookie_handler=CookieHandler(None),
    )


def _patch_send(monkeypatch: pytest.MonkeyPatch, fail_times: int) -> dict[str, int]:
    """Replace ``httpx.Client.send`` with a transport that times out the first
    *fail_times* attempts, then returns a 200. Returns the attempt counter."""
    attempts = {"n": 0}

    def fake_send(self: Any, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] <= fail_times:
            raise httpx.ReadTimeout("simulated read timeout", request=request)
        return httpx.Response(200, content=b"{}", request=request)

    monkeypatch.setattr(httpx.Client, "send", fake_send)
    return attempts


def test_timeout_retries_engage_after_httpx_timeout(monkeypatch: pytest.MonkeyPatch):
    """A raw httpx timeout is translated so the retry loop makes >1 attempt."""
    attempts = _patch_send(monkeypatch, fail_times=2)

    policy = RetryPolicy(
        timeout_retries=3,
        http_retries=0,
        interruptible_sleep=lambda _s: None,
    )
    dispatcher = _make_dispatcher()
    req = Request(method="GET", url="https://api.example.com/users")

    response = policy.execute(lambda: dispatcher.execute(request=req, headers={}))

    assert attempts["n"] == 3  # two timeouts + one success
    assert response.status_code == 200
    assert policy.get_retry_summary() == "retried 2\u00d7 after timeout"


def test_timeout_retries_exhaustion_raises_domain_error(
    monkeypatch: pytest.MonkeyPatch,
):
    """When every attempt times out, RequestTimeoutError escapes the loop."""
    attempts = _patch_send(monkeypatch, fail_times=99)

    policy = RetryPolicy(
        timeout_retries=3,
        http_retries=0,
        interruptible_sleep=lambda _s: None,
    )
    dispatcher = _make_dispatcher()
    req = Request(method="GET", url="https://api.example.com/users")

    with pytest.raises(RequestTimeoutError, match="timed out"):
        policy.execute(lambda: dispatcher.execute(request=req, headers={}))

    assert attempts["n"] == 3  # all timeout attempts consumed
