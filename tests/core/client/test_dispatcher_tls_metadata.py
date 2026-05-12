import httpx
import pytest

from equinox.core.client.cookie_handler import CookieHandler
from equinox.core.client.dispatcher import HttpxDispatcher
from equinox.core.client.http_client import HTTPClient
from equinox.core.request import Request


class _FakeSSLObject:
    def version(self):
        return "TLSv1.3"

    def cipher(self):
        return ("TLS_AES_256_GCM_SHA384", "TLSv1.3", 256)

    def getpeercert(self):
        return {
            "subject": ((('commonName', 'api.example.com'),),),
            "issuer": ((('commonName', 'Example CA'),),),
            "notBefore": "Jan  1 00:00:00 2026 GMT",
            "notAfter": "Jan  1 00:00:00 2027 GMT",
            "serialNumber": "1234ABCD",
            "subjectAltName": (("DNS", "api.example.com"), ("DNS", "www.example.com")),
        }


class _FakeStream:
    def __init__(self):
        self._extras = {
            "ssl_object": _FakeSSLObject(),
            "server_addr": ("203.0.113.10", 443),
        }

    def get_extra_info(self, name):
        return self._extras.get(name)


def _make_dispatcher() -> HttpxDispatcher:
    return HttpxDispatcher(
        timeout=10.0,
        follow_redirects=True,
        verify_ssl=True,
        proxy=None,
        cookie_handler=CookieHandler(None),
    )


def test_extract_tls_info_from_stream():
    info = HttpxDispatcher._extract_tls_info_from_stream(_FakeStream())

    assert info["tls_version"] == "TLSv1.3"
    assert info["cipher"] == "TLS_AES_256_GCM_SHA384"
    assert info["cipher_bits"] == 256
    assert info["cert_subject"] == "api.example.com"
    assert info["cert_issuer"] == "Example CA"
    assert info["cert_serial"] == "1234ABCD"
    assert info["cert_san_count"] == 2


def test_wrap_response_attaches_connection_info_and_sent_url():
    dispatcher = _make_dispatcher()
    req = Request(method="GET", url="https://api.example.com/users")

    raw_req = httpx.Request("GET", "https://api.example.com/users")
    raw_resp = httpx.Response(
        200,
        headers={"content-type": "application/json"},
        content=b"{}",
        request=raw_req,
        extensions={"network_stream": _FakeStream()},
    )

    wrapped = dispatcher._wrap_response(raw_resp, req, elapsed=0.01, sent_headers={"accept": "application/json"})

    assert wrapped.sent_url == "https://api.example.com/users"
    assert wrapped.connection_info is not None
    assert wrapped.connection_info.get("tls_version") == "TLSv1.3"
    assert wrapped.connection_info.get("cert_subject") == "api.example.com"
    assert wrapped.connection_info.get("server_addr") == "('203.0.113.10', 443)"


def test_wrap_response_redacts_sensitive_sent_headers():
    dispatcher = _make_dispatcher()
    req = Request(method="GET", url="https://api.example.com/users")

    raw_req = httpx.Request("GET", "https://api.example.com/users")
    raw_resp = httpx.Response(
        200,
        headers={"content-type": "application/json"},
        content=b"{}",
        request=raw_req,
    )

    wrapped = dispatcher._wrap_response(
        raw_resp,
        req,
        elapsed=0.01,
        sent_headers={"Authorization": "Bearer secret", "accept": "application/json"},
    )

    assert wrapped.sent_headers["Authorization"] == "[REDACTED]"
    assert wrapped.sent_headers["accept"] == "application/json"


def test_wrap_response_preserves_repeated_set_cookie_headers():
    dispatcher = _make_dispatcher()
    req = Request(method="GET", url="https://api.example.com/users")

    raw_req = httpx.Request("GET", "https://api.example.com/users")
    headers = httpx.Headers(
        [
            (b"Set-Cookie", b"a=1; Path=/"),
            (b"Set-Cookie", b"b=2; Path=/"),
        ]
    )
    raw_resp = httpx.Response(200, headers=headers, content=b"{}", request=raw_req)

    wrapped = dispatcher._wrap_response(raw_resp, req, elapsed=0.01, sent_headers={})
    assert wrapped.set_cookie_headers == ["a=1; Path=/", "b=2; Path=/"]


def test_ensure_client_keeps_verify_ssl_isolated(monkeypatch: pytest.MonkeyPatch):
    calls = []

    class _FakeClient:
        def __init__(self, **kwargs):
            calls.append(kwargs)
            self.cookies = type(
                "_C",
                (),
                {
                    "clear": lambda self: None,
                    "set": lambda self, *args, **kwargs: None,
                },
            )()

        def close(self):
            return None

    monkeypatch.setattr("equinox.core.client.dispatcher.httpx.Client", _FakeClient)

    dispatcher = _make_dispatcher()
    c1 = dispatcher._ensure_client(True)
    c2 = dispatcher._ensure_client(False)
    c3 = dispatcher._ensure_client(True)

    assert c1 is c3
    assert c1 is not c2
    assert len(calls) == 2
    assert calls[0]["verify"] is not False
    assert calls[1]["verify"] is False


def test_dispatcher_applies_cookie_scope_by_domain():
    class _ScopedManager:
        def to_httpx_cookies(self):
            return {}

        def to_httpx_cookie_records(self):
            return [
                {
                    "name": "session",
                    "value": "abc",
                    "domain": "example.com",
                    "path": "/",
                }
            ]

    dispatcher = HttpxDispatcher(
        timeout=10.0,
        follow_redirects=True,
        verify_ssl=True,
        proxy=None,
        cookie_handler=CookieHandler(_ScopedManager()),
    )
    client = dispatcher._ensure_client()

    req_same = client.build_request("GET", "https://example.com/path")
    req_other = client.build_request("GET", "https://other.com/path")

    assert req_same.headers.get("cookie") == "session=abc"
    assert req_other.headers.get("cookie") is None


def test_proxy_credentials_redacted_in_repr():
    proxy = "http://user:secret@example-proxy.local:8080"

    dispatcher = HttpxDispatcher(
        timeout=10.0,
        follow_redirects=True,
        verify_ssl=True,
        proxy=proxy,
        cookie_handler=CookieHandler(None),
    )
    client = HTTPClient(proxy=proxy)

    assert "secret" not in repr(dispatcher)
    assert "secret" not in repr(client)
    assert "***:***" in repr(dispatcher)
    assert "***:***" in repr(client)


