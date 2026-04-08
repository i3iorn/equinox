"""Tests for equinox.core.codegen — code generation from Request objects."""

import pytest

from equinox.core.request import Request
from equinox.core.codegen import (
    PythonRequestsGenerator,
    PythonHttpxGenerator,
    JavaScriptFetchGenerator,
    GoHttpGenerator,
    generate_code,
    GENERATORS,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _req(**kwargs) -> Request:
    defaults = dict(method="GET", url="https://api.example.com/users")
    defaults.update(kwargs)
    return Request(**defaults)


def _post_req() -> Request:
    return Request(
        method="POST",
        url="https://api.example.com/users",
        headers={"Content-Type": "application/json"},
        body='{"name": "Alice", "age": 30}',
    )


# ── PythonRequestsGenerator ───────────────────────────────────────────────────

class TestPythonRequestsGenerator:
    def test_get_contains_method_and_url(self):
        out = PythonRequestsGenerator().generate(_req())
        assert "requests.get" in out
        assert "https://api.example.com/users" in out

    def test_post_contains_method(self):
        out = PythonRequestsGenerator().generate(_post_req())
        assert "requests.post" in out

    def test_headers_included(self):
        req = _req(headers={"X-Custom": "value123"})
        out = PythonRequestsGenerator().generate(req)
        assert "X-Custom" in out
        assert "value123" in out

    def test_body_json_included(self):
        out = PythonRequestsGenerator().generate(_post_req())
        assert "json_body" in out or "application/json" in out or "Alice" in out

    def test_bearer_auth_in_header(self):
        from equinox.auth import BearerAuth
        req = _req(auth=BearerAuth(token="mytoken123"))
        out = PythonRequestsGenerator().generate(req)
        assert "Authorization" in out
        assert "Bearer <YOUR_TOKEN>" in out

    def test_basic_auth_kwarg(self):
        from equinox.auth import BasicAuth
        req = _req(auth=BasicAuth(username="user", password="pass"))
        out = PythonRequestsGenerator().generate(req)
        assert "auth=" in out
        assert "<YOUR_USERNAME>" in out

    def test_api_key_in_header(self):
        from equinox.auth import APIKeyAuth
        req = _req(auth=APIKeyAuth(key="X-API-Key", value="secret", location="header"))
        out = PythonRequestsGenerator().generate(req)
        assert "X-API-Key" in out
        assert "<YOUR_API_KEY>" in out

    def test_params_included(self):
        req = _req(params={"page": "1", "limit": "10"})
        out = PythonRequestsGenerator().generate(req)
        assert "params" in out
        assert "page" in out

    def test_import_line_present(self):
        out = PythonRequestsGenerator().generate(_req())
        assert "import requests" in out


# ── PythonHttpxGenerator ──────────────────────────────────────────────────────

class TestPythonHttpxGenerator:
    def test_uses_httpx(self):
        out = PythonHttpxGenerator().generate(_req())
        assert "import httpx" in out
        assert "httpx.Client" in out

    def test_get_method(self):
        out = PythonHttpxGenerator().generate(_req())
        assert "client.get" in out

    def test_post_method(self):
        out = PythonHttpxGenerator().generate(_post_req())
        assert "client.post" in out

    def test_headers_present(self):
        req = _req(headers={"Authorization": "Bearer tok"})
        out = PythonHttpxGenerator().generate(req)
        assert "Authorization" in out

    def test_body_included(self):
        out = PythonHttpxGenerator().generate(_post_req())
        assert "Alice" in out or "json_body" in out

    def test_bearer_injected(self):
        from equinox.auth import BearerAuth
        req = _req(auth=BearerAuth(token="httpxtoken"))
        out = PythonHttpxGenerator().generate(req)
        assert "Bearer <YOUR_TOKEN>" in out

    def test_basic_auth_kwarg(self):
        from equinox.auth import BasicAuth
        req = _req(auth=BasicAuth(username="u", password="p"))
        out = PythonHttpxGenerator().generate(req)
        assert "auth=" in out
        assert "<YOUR_USERNAME>" in out

    def test_url_present(self):
        out = PythonHttpxGenerator().generate(_req())
        assert "https://api.example.com/users" in out


# ── JavaScriptFetchGenerator ──────────────────────────────────────────────────

class TestJavaScriptFetchGenerator:
    def test_uses_fetch(self):
        out = JavaScriptFetchGenerator().generate(_req())
        assert "fetch(" in out

    def test_method_included(self):
        out = JavaScriptFetchGenerator().generate(_req(method="DELETE"))
        assert "DELETE" in out

    def test_url_included(self):
        out = JavaScriptFetchGenerator().generate(_req())
        assert "https://api.example.com/users" in out

    def test_headers_included(self):
        req = _req(headers={"X-Token": "abc"})
        out = JavaScriptFetchGenerator().generate(req)
        assert "X-Token" in out

    def test_body_json_stringify(self):
        out = JavaScriptFetchGenerator().generate(_post_req())
        assert "JSON.stringify" in out or "jsonBody" in out

    def test_params_appended_to_url(self):
        req = _req(params={"q": "test"})
        out = JavaScriptFetchGenerator().generate(req)
        assert "q=test" in out

    def test_bearer_in_headers(self):
        from equinox.auth import BearerAuth
        req = _req(auth=BearerAuth(token="jstoken"))
        out = JavaScriptFetchGenerator().generate(req)
        assert "Bearer <YOUR_TOKEN>" in out

    def test_basic_auth_redacted(self):
        from equinox.auth import BasicAuth
        req = _req(auth=BasicAuth(username="alice", password="secret"))
        out = JavaScriptFetchGenerator().generate(req)
        assert "<YOUR_TOKEN>" in out
        # Real credentials should NOT appear in generated code
        assert "alice" not in out
        assert "secret" not in out


# ── GoHttpGenerator ───────────────────────────────────────────────────────────

class TestGoHttpGenerator:
    def test_package_main(self):
        out = GoHttpGenerator().generate(_req())
        assert "package main" in out

    def test_imports_net_http(self):
        out = GoHttpGenerator().generate(_req())
        assert '"net/http"' in out

    def test_method_and_url(self):
        out = GoHttpGenerator().generate(_req(method="PUT"))
        assert "PUT" in out
        assert "https://api.example.com/users" in out

    def test_body_uses_strings(self):
        out = GoHttpGenerator().generate(_post_req())
        assert "strings.NewReader" in out

    def test_headers_set(self):
        req = _req(headers={"Accept": "application/json"})
        out = GoHttpGenerator().generate(req)
        assert "req.Header.Set" in out
        assert "Accept" in out

    def test_bearer_header(self):
        from equinox.auth import BearerAuth
        req = _req(auth=BearerAuth(token="gotoken"))
        out = GoHttpGenerator().generate(req)
        assert "Bearer <YOUR_TOKEN>" in out

    def test_no_body_uses_nil(self):
        out = GoHttpGenerator().generate(_req())
        assert "nil" in out


# ── generate_code() dispatcher ────────────────────────────────────────────────

class TestGenerateCodeDispatcher:
    @pytest.mark.parametrize("fmt", list(GENERATORS.keys()))
    def test_each_format_works(self, fmt):
        req = _post_req()
        code = generate_code(fmt, req)
        assert isinstance(code, str)
        assert len(code) > 10

    def test_unknown_format_raises(self):
        with pytest.raises(KeyError):
            generate_code("COBOL", _req())

    def test_python_requests_smoke(self):
        req = Request(
            method="POST",
            url="https://api.example.com/users",
            headers={"Content-Type": "application/json"},
            body='{"name": "Alice"}',
        )
        out = generate_code("Python (requests)", req)
        assert "requests.post" in out
        assert "application/json" in out
