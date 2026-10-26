"""Integration tests pinning redirect-leg auth behavior (issue #64).

``_RedirectSafeAuth`` rides on httpx's native auth path.  Its earlier
docstring claimed ``auth_flow`` was re-executed on every redirect leg, which
is factually wrong for yield-once flows — httpx executes the flow once and
strips ``Authorization`` on cross-origin hops itself (scheme upgrades
excepted), and always drops ``Cookie`` on hops (cookie store governs).

These tests pin the ACTUAL behavior so the false claim (and any real
credential-leak regression) cannot return unnoticed.
"""

from __future__ import annotations

import httpx

from equinox.core.client.auth_redirect import _RedirectSafeAuth

_TOKEN = "Bearer integration-probe-token-1"


def _send(follow_redirects: bool = True, probe_header: str = "Authorization", **auth_headers: str):
    """Send through a MockTransport that records *probe_header* per leg."""
    legs: list[tuple[str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        legs.append(
            (f"{request.url.scheme}://{request.url.host}", request.headers.get(probe_header)),
        )
        if request.url.host == "legit.example":
            return httpx.Response(
                302,
                headers={"Location": "https://attacker.example/api"},
                text="",
            )
        return httpx.Response(200, text="attacker-ok")

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    request = httpx.Request("GET", "http://legit.example/api")
    client.send(request, follow_redirects=follow_redirects, auth=_RedirectSafeAuth(auth_headers))
    return legs


class TestCrossOriginRedirectStripsAuth:
    """Cross-origin redirect hops must NOT carry credentials."""

    def test_authorization_absent_on_cross_origin_hop(self) -> None:
        legs = _send(Authorization=_TOKEN)
        assert legs[0] == ("http://legit.example", _TOKEN)
        assert legs[1] == ("https://attacker.example", None)

    def test_cookie_always_dropped_on_hop(self) -> None:
        legs = _send(probe_header="Cookie", Cookie="session=abc123")
        assert legs[0][1] == "session=abc123"
        assert legs[1][1] is None

    def test_authorization_header_name_case_insensitive(self) -> None:
        legs = _send(AUTHORIZATION=_TOKEN)
        assert legs[1][1] is None


class TestSchemeUpgradeKeepsAuth:
    """http->https on the same host is the adapter's documented OAuth2 use case."""

    def test_authorization_survives_http_to_https_redirect(self) -> None:
        legs: list[tuple[str, str | None]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            legs.append(
                (
                    f"{request.url.scheme}://{request.url.host}",
                    request.headers.get("Authorization"),
                ),
            )
            if request.url.scheme == "http":
                return httpx.Response(
                    302,
                    headers={"Location": "https://legit.example/api"},
                    text="",
                )
            return httpx.Response(200, text="ok")

        transport = httpx.MockTransport(handler)
        client = httpx.Client(transport=transport)
        request = httpx.Request("GET", "http://legit.example/api")
        client.send(
            request,
            follow_redirects=True,
            auth=_RedirectSafeAuth({"Authorization": _TOKEN}),
        )
        assert legs[0] == ("http://legit.example", _TOKEN)
        assert legs[1] == ("https://legit.example", _TOKEN)


class TestSameOriginRedirectKeepsAuth:
    def test_authorization_survives_same_origin_redirect(self) -> None:
        legs: list[tuple[str, str | None]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            legs.append(
                (
                    f"{request.url.scheme}://{request.url.host}",
                    request.headers.get("Authorization"),
                ),
            )
            if request.url.path == "/api":
                return httpx.Response(
                    302,
                    headers={"Location": "https://legit.example/other"},
                    text="",
                )
            return httpx.Response(200, text="ok")

        transport = httpx.MockTransport(handler)
        client = httpx.Client(transport=transport)
        request = httpx.Request("GET", "https://legit.example/api")
        client.send(
            request,
            follow_redirects=True,
            auth=_RedirectSafeAuth({"Authorization": _TOKEN}),
        )
        assert legs[0][1] == _TOKEN
        assert legs[1][1] == _TOKEN


class TestAuthFlowExecutedOnce:
    """The docstring's false claim, pinned as a contract."""

    def test_yield_once_flow_invoked_single_time(self) -> None:
        calls: list[str] = []

        class CountingAuth(httpx.Auth):
            def auth_flow(self, request):
                calls.append("invoked")
                for k, v in {"Authorization": _TOKEN}.items():
                    request.headers[k] = v
                yield request

        legs = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            legs["n"] += 1
            if legs["n"] == 1:
                return httpx.Response(
                    302,
                    headers={"Location": "https://legit.example/other"},
                    text="",
                )
            return httpx.Response(200, text="ok")

        transport = httpx.MockTransport(handler)
        client = httpx.Client(transport=transport)
        request = httpx.Request("GET", "https://legit.example/api")
        client.send(request, follow_redirects=True, auth=CountingAuth())
        assert calls == ["invoked"]
