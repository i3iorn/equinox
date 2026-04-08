"""Tests for core/error_enrichment.py — rich error conversion."""

import httpx
import pytest
from unittest.mock import Mock

from equinox.core.error_enrichment import (
    RichError,
    enrich_exception,
    _enrich_httpx_error,
    _enrich_equinox_error,
    _describe_connect_error,
)
from equinox.core.exceptions import (
    TimeoutError as EqTimeoutError,
    RequestError,
    ValidationError,
    AuthError,
)


# ── enrich_exception (top-level) ──────────────────────────────────────────


class TestEnrichException:
    def test_returns_rich_error(self):
        result = enrich_exception(ValueError("boom"))
        assert isinstance(result, RichError)
        assert result.exc_type == "ValueError"
        assert "boom" in result.message

    def test_empty_message_produces_unexpected(self):
        result = enrich_exception(RuntimeError(""))
        assert "Unexpected error" in result.message
        assert "RuntimeError" in result.message

    def test_traceback_populated(self):
        try:
            raise KeyError("missing")
        except KeyError as exc:
            result = enrich_exception(exc)
        assert result.tb  # non-empty string


# ── _enrich_httpx_error ───────────────────────────────────────────────────


class TestEnrichHttpxError:
    def test_connect_timeout(self):
        exc = httpx.ConnectTimeout("timed out")
        msg = _enrich_httpx_error(exc, str(exc), "ConnectTimeout")
        assert "Connection timed out" in msg

    def test_read_timeout(self):
        exc = httpx.ReadTimeout("read timed out")
        msg = _enrich_httpx_error(exc, str(exc), "ReadTimeout")
        assert "read timeout" in msg

    def test_connect_error_generic(self):
        exc = httpx.ConnectError("something broke")
        msg = _enrich_httpx_error(exc, str(exc), "ConnectError")
        assert "Could not connect" in msg

    def test_too_many_redirects(self):
        exc = httpx.TooManyRedirects("too many")
        msg = _enrich_httpx_error(exc, str(exc), "TooManyRedirects")
        assert "redirect" in msg.lower()

    def test_generic_timeout_exception(self):
        exc = httpx.TimeoutException("timeout")
        msg = _enrich_httpx_error(exc, str(exc), "TimeoutException")
        assert "timed out" in msg

    def test_http_status_error(self):
        mock_resp = Mock()
        mock_resp.status_code = 503
        exc = httpx.HTTPStatusError("err", request=Mock(), response=mock_resp)
        msg = _enrich_httpx_error(exc, str(exc), "HTTPStatusError")
        assert "503" in msg

    def test_invalid_url(self):
        exc = httpx.InvalidURL("bad url")
        msg = _enrich_httpx_error(exc, str(exc), "InvalidURL")
        assert "Invalid URL" in msg

    def test_generic_http_error(self):
        exc = httpx.HTTPError("generic")
        msg = _enrich_httpx_error(exc, "generic", "HTTPError")
        assert "HTTP error" in msg

    def test_generic_http_error_empty_raw(self):
        exc = httpx.HTTPError("")
        msg = _enrich_httpx_error(exc, "", "HTTPError")
        assert "HTTPError" in msg

    def test_non_httpx_returns_none(self):
        exc = ValueError("not httpx")
        assert _enrich_httpx_error(exc, str(exc), "ValueError") is None


# ── _describe_connect_error ───────────────────────────────────────────────


class TestDescribeConnectError:
    def test_ssl_error(self):
        msg = _describe_connect_error("SSL: CERTIFICATE_VERIFY_FAILED")
        assert "SSL/TLS error" in msg
        assert "certificate" in msg.lower()

    def test_certificate_keyword(self):
        msg = _describe_connect_error("certificate has expired")
        assert "SSL/TLS error" in msg

    def test_dns_name_not_known(self):
        msg = _describe_connect_error("[Errno -2] Name or service not known")
        assert "DNS lookup failed" in msg

    def test_dns_nodename(self):
        msg = _describe_connect_error("nodename nor servname provided")
        assert "DNS lookup failed" in msg

    def test_connection_refused(self):
        msg = _describe_connect_error("[Errno 111] Connection refused")
        assert "Connection refused" in msg

    def test_generic_connect_error(self):
        msg = _describe_connect_error("some unknown error")
        assert "Could not connect" in msg

    def test_empty_inner(self):
        msg = _describe_connect_error("")
        assert "no additional detail" in msg


# ── _enrich_equinox_error ─────────────────────────────────────────────────


class TestEnrichEquinoxError:
    def test_timeout_with_details(self):
        exc = EqTimeoutError("timed out", details={"timeout": 30})
        msg = _enrich_equinox_error(exc, str(exc), "TimeoutError")
        assert "timed out" in msg
        assert "30s" in msg

    def test_timeout_without_details(self):
        exc = EqTimeoutError("timed out")
        msg = _enrich_equinox_error(exc, str(exc), "TimeoutError")
        assert "timed out" in msg

    def test_auth_error(self):
        exc = AuthError("bad creds")
        msg = _enrich_equinox_error(exc, "bad creds", "AuthError")
        assert "Authentication failed" in msg
        assert "bad creds" in msg

    def test_auth_error_empty(self):
        exc = AuthError("")
        msg = _enrich_equinox_error(exc, "", "AuthError")
        assert "check your credentials" in msg

    def test_validation_error(self):
        exc = ValidationError("invalid URL")
        msg = _enrich_equinox_error(exc, "invalid URL", "ValidationError")
        assert "Validation error" in msg
        assert "invalid URL" in msg

    def test_request_error(self):
        exc = RequestError("connection failed")
        msg = _enrich_equinox_error(exc, "connection failed", "RequestError")
        assert "connection failed" in msg

    def test_request_error_empty(self):
        exc = RequestError("")
        msg = _enrich_equinox_error(exc, "", "RequestError")
        assert "Request failed" in msg

    def test_non_equinox_returns_none(self):
        exc = ValueError("not equinox")
        assert _enrich_equinox_error(exc, str(exc), "ValueError") is None

