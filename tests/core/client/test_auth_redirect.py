"""100% coverage tests for equinox.core.client.auth_redirect"""

import logging
import pytest
import httpx

from equinox.core.client.auth_redirect import _RedirectSafeAuth


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_httpx_request(method: str = "GET", url: str = "https://example.com") -> httpx.Request:
    return httpx.Request(method, url)


def _run_auth_flow(auth: _RedirectSafeAuth, request: httpx.Request) -> httpx.Request:
    """Drive the auth_flow generator to completion and return the yielded request."""
    gen = auth.auth_flow(request)
    yielded = next(gen)
    # Simulate httpx sending the request and returning a response
    try:
        gen.send(httpx.Response(200))
    except StopIteration:
        pass
    return yielded


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------

class TestRedirectSafeAuthInit:
    def test_empty_dict_raises_value_error(self):
        with pytest.raises(ValueError, match="auth_headers must not be empty"):
            _RedirectSafeAuth({})

    def test_none_equivalent_empty_raises(self):
        # Any falsy mapping must raise
        with pytest.raises(ValueError):
            _RedirectSafeAuth({})

    def test_single_header_accepted(self):
        auth = _RedirectSafeAuth({"Authorization": "Bearer tok"})
        assert auth._auth_headers == {"Authorization": "Bearer tok"}

    def test_multiple_headers_accepted(self):
        headers = {"Authorization": "Bearer tok", "X-Api-Key": "key123"}
        auth = _RedirectSafeAuth(headers)
        assert auth._auth_headers == headers

    def test_defensive_copy_made(self):
        """Mutating the original dict must not affect the stored headers."""
        original = {"Authorization": "Bearer tok"}
        auth = _RedirectSafeAuth(original)
        original["Authorization"] = "CHANGED"
        assert auth._auth_headers["Authorization"] == "Bearer tok"


# ---------------------------------------------------------------------------
# auth_flow
# ---------------------------------------------------------------------------

class TestRedirectSafeAuthFlow:
    def test_single_header_injected(self):
        auth = _RedirectSafeAuth({"Authorization": "Bearer tok"})
        request = _make_httpx_request()
        _run_auth_flow(auth, request)
        assert request.headers["Authorization"] == "Bearer tok"

    def test_multiple_headers_all_injected(self):
        auth = _RedirectSafeAuth({"Authorization": "Bearer tok", "X-Key": "val"})
        request = _make_httpx_request()
        _run_auth_flow(auth, request)
        assert request.headers["Authorization"] == "Bearer tok"
        assert request.headers["X-Key"] == "val"

    def test_existing_header_overwritten(self):
        auth = _RedirectSafeAuth({"Authorization": "Bearer new"})
        request = _make_httpx_request("GET", "https://example.com")
        request.headers["Authorization"] = "Bearer old"
        _run_auth_flow(auth, request)
        assert request.headers["Authorization"] == "Bearer new"

    def test_yields_the_same_request_object(self):
        auth = _RedirectSafeAuth({"Authorization": "Bearer tok"})
        request = _make_httpx_request()
        gen = auth.auth_flow(request)
        yielded = next(gen)
        assert yielded is request

    def test_generator_exhausted_after_one_yield(self):
        auth = _RedirectSafeAuth({"Authorization": "Bearer tok"})
        request = _make_httpx_request()
        gen = auth.auth_flow(request)
        next(gen)
        with pytest.raises(StopIteration):
            next(gen)

    def test_logs_debug_message(self, caplog):
        auth = _RedirectSafeAuth({"Authorization": "Bearer tok", "X-Key": "v"})
        request = _make_httpx_request("POST", "https://api.example.com/data")
        with caplog.at_level(logging.DEBUG, logger="equinox.core.client.auth_redirect"):
            _run_auth_flow(auth, request)
        assert "_RedirectSafeAuth" in caplog.text
        assert "2" in caplog.text  # 2 headers injected

    def test_debug_log_includes_method_and_url(self, caplog):
        auth = _RedirectSafeAuth({"Authorization": "Bearer tok"})
        request = _make_httpx_request("DELETE", "https://api.example.com/resource")
        with caplog.at_level(logging.DEBUG, logger="equinox.core.client.auth_redirect"):
            _run_auth_flow(auth, request)
        assert "DELETE" in caplog.text
        assert "api.example.com" in caplog.text

    def test_is_valid_httpx_auth_subclass(self):
        auth = _RedirectSafeAuth({"Authorization": "Bearer tok"})
        assert isinstance(auth, httpx.Auth)


# ---------------------------------------------------------------------------
# __repr__
# ---------------------------------------------------------------------------

class TestRedirectSafeAuthRepr:
    def test_repr_contains_sorted_keys(self):
        auth = _RedirectSafeAuth({"Z-Header": "a", "A-Header": "b"})
        r = repr(auth)
        assert "_RedirectSafeAuth" in r
        assert "A-Header" in r
        assert "Z-Header" in r
        # Keys appear in sorted order
        assert r.index("A-Header") < r.index("Z-Header")

    def test_repr_single_key(self):
        auth = _RedirectSafeAuth({"Authorization": "tok"})
        assert repr(auth) == "_RedirectSafeAuth(headers=['Authorization'])"

    def test_repr_multiple_keys_sorted(self):
        auth = _RedirectSafeAuth({"X-Key": "x", "Authorization": "tok"})
        assert repr(auth) == "_RedirectSafeAuth(headers=['Authorization', 'X-Key'])"

