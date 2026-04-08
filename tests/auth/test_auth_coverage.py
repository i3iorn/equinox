"""Tests to achieve 100% coverage on the auth module.

Covers every uncovered line in:
- api_key.py (line 42)
- aws_sigv4.py (lines 86, 175)
- base.py (lines 55, 60, 66)
- factory.py (lines 44-45, 77-78, 82-84)
- oauth2.py (lines 98, 113, 181, 187-188, 199-218, 226-239, 246-250,
             261, 279-283, 295-296, 300-301, 345, 364-365, 396-437,
             450-454, 458, 492-493)
"""

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict
from unittest.mock import MagicMock, Mock, patch, PropertyMock

import httpx
import pytest

from equinox.auth.api_key import APIKeyAuth
from equinox.auth.aws_sigv4 import AWSSigV4Auth
from equinox.auth.base import AuthStrategy, _validate_credential
from equinox.auth.factory import AUTH_REGISTRY, auth_from_dict
from equinox.auth.oauth2 import OAuth2Auth, _DEFAULT_TOKEN_EXPIRY_SECONDS
from equinox.core.exceptions import AuthError


# ── api_key.py — line 42: request without .params attribute ───────────────────


class TestAPIKeyCoverage:
    def test_query_location_creates_params_when_missing(self):
        """Line 42: request.params = {} when request has no params attr."""
        auth = APIKeyAuth("api_key", "secret-123", location="query")
        request = Mock(spec=[])  # no attributes at all
        headers = {}
        auth.apply(request, headers)
        assert request.params["api_key"] == "secret-123"


# ── aws_sigv4.py — lines 86, 175 ─────────────────────────────────────────────


class TestAWSSigV4Coverage:
    def test_non_standard_port_included_in_host(self):
        """Line 86: host gets port appended for non-80/443 ports."""
        auth = AWSSigV4Auth("AKID", "secret", "us-east-1", "s3")
        req = MagicMock()
        req.url = "https://s3.amazonaws.com:8443/bucket/key"
        req.method = "GET"
        req.body = None
        headers: Dict[str, str] = {}
        auth.apply(req, headers)
        assert "8443" in headers["host"]

    def test_canonical_uri_empty_path(self):
        """Line 175: empty path returns '/'."""
        assert AWSSigV4Auth._canonical_uri("") == "/"

    def test_apply_url_without_path(self):
        """URL with no path component exercises empty-path branch."""
        auth = AWSSigV4Auth("AKID", "secret", "us-east-1", "s3")
        req = MagicMock()
        req.url = "https://s3.amazonaws.com"
        req.method = "GET"
        req.body = None
        headers: Dict[str, str] = {}
        auth.apply(req, headers)
        assert "Authorization" in headers


# ── base.py — lines 55, 60, 66: abstract method pass bodies ──────────────────


class _ConcreteAuth(AuthStrategy):
    """Minimal concrete subclass for testing abstract base."""

    def apply(self, request: Any, headers: Dict[str, str]) -> None:
        super().apply(request, headers)

    def to_dict(self) -> Dict[str, Any]:
        result = super().to_dict()
        return result  # type: ignore[return-value]

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AuthStrategy":
        result = AuthStrategy.from_dict(data)
        return result  # type: ignore[return-value]


class TestAuthBaseCoverage:
    def test_abstract_apply_body(self):
        """Line 55: abstract apply() body executes."""
        auth = _ConcreteAuth()
        auth.apply(Mock(), {})

    def test_abstract_to_dict_body(self):
        """Line 60: abstract to_dict() body executes."""
        auth = _ConcreteAuth()
        assert auth.to_dict() is None

    def test_abstract_from_dict_body(self):
        """Line 66: abstract from_dict() body executes."""
        result = _ConcreteAuth.from_dict({})
        assert result is None


# ── factory.py — lines 44-45, 77-78, 82-84 ───────────────────────────────────


class TestAuthFactoryCoverage:
    def test_aws_sigv4_via_factory(self):
        """Lines 44-45: _get_aws_sigv4 lazy loader exercised."""
        result = auth_from_dict(
            "aws_sigv4",
            {
                "access_key": "K",
                "secret_key": "S",
                "region": "us-east-1",
                "service": "s3",
            },
        )
        assert isinstance(result, AWSSigV4Auth)

    def test_unknown_auth_type_raises(self):
        """Lines 77-78: unknown type raises ValueError."""
        with pytest.raises(ValueError, match="Unknown auth type"):
            auth_from_dict("nonexistent_type", {})

    def test_from_dict_failure_returns_none(self):
        """Lines 82-84: exception during from_dict returns None."""
        # BearerAuth.from_dict needs "token" key — passing empty triggers KeyError
        result = auth_from_dict("bearer", {})
        assert result is None

    def test_class_name_key_works(self):
        """AUTH_REGISTRY also accepts class name strings."""
        result = auth_from_dict(
            "AWSSigV4Auth",
            {
                "access_key": "K",
                "secret_key": "S",
                "region": "us-east-1",
                "service": "s3",
            },
        )
        assert isinstance(result, AWSSigV4Auth)


# ── oauth2.py — comprehensive coverage ───────────────────────────────────────


class TestOAuth2Coverage:
    """Tests targeting every uncovered line in oauth2.py."""

    # ── Line 98: _load_from_storage when secure_storage is configured ─────

    def test_load_from_storage_on_init(self):
        """Line 98: _load_from_storage called when secure_storage + storage_key set."""
        storage = MagicMock()
        storage.retrieve.return_value = json.dumps(
            {
                "access_token": "stored-tok",
                "refresh_token": "stored-rt",
                "expires_at": "2099-01-01T00:00:00",
            }
        )
        auth = OAuth2Auth(
            client_id="c",
            secure_storage=storage,
            storage_key="my_key",
        )
        assert auth.access_token == "stored-tok"
        assert auth.refresh_token == "stored-rt"
        assert auth.expires_at is not None

    def test_load_from_storage_empty(self):
        """Lines 215-216: storage returns None/empty."""
        storage = MagicMock()
        storage.retrieve.return_value = None
        auth = OAuth2Auth(
            client_id="c",
            secure_storage=storage,
            storage_key="my_key",
        )
        assert auth.access_token is None

    def test_load_from_storage_failure(self):
        """Lines 217-218: storage.retrieve raises — logged, not fatal."""
        storage = MagicMock()
        storage.retrieve.side_effect = RuntimeError("disk error")
        auth = OAuth2Auth(
            client_id="c",
            secure_storage=storage,
            storage_key="my_key",
        )
        # Should not raise; access_token stays None
        assert auth.access_token is None

    def test_load_from_storage_no_storage_no_key(self):
        """Lines 199-200: no secure_storage → early return, debug log."""
        auth = OAuth2Auth(client_id="c")
        # Manually call to exercise the guard
        auth.secure_storage = None
        auth._load_from_storage()
        # No error expected

    # ── Line 113: no access token after apply without refresh ─────────────

    def test_apply_no_token_raises(self):
        """Line 113: raise AuthError when no access token available."""
        auth = OAuth2Auth(access_token=None, token_url=None)
        # _needs_refresh will be True, but no token_url → _refresh raises first.
        # To hit line 113 specifically, we mock _needs_refresh to return False.
        with patch.object(auth, "_needs_refresh", return_value=False):
            with pytest.raises(AuthError, match="No access token available"):
                auth.apply(Mock(), {})

    # ── Line 181: expires_at with timezone info ───────────────────────────

    def test_needs_refresh_with_tz_aware_expiry(self):
        """Line 181: expiry normalised when tzinfo is present."""
        auth = OAuth2Auth(access_token="tok")
        auth.expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
        assert not auth._needs_refresh()

    def test_needs_refresh_with_tz_aware_expired(self):
        """Line 181: tz-aware expiry that is already past → needs refresh."""
        auth = OAuth2Auth(access_token="tok")
        auth.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
        assert auth._needs_refresh()

    # ── Lines 187-188: get_token_info ─────────────────────────────────────

    def test_get_token_info_with_token(self):
        """Lines 187-193: get_token_info returns summary dict."""
        auth = OAuth2Auth(
            access_token="my-secret-token",
            refresh_token="rt",
        )
        auth.expires_at = datetime(2099, 1, 1)
        info = auth.get_token_info()
        assert info["has_refresh_token"] is True
        assert info["expires_at"] is not None
        assert info["needs_refresh"] is False
        assert "my-secret-token" not in info["access_token"]  # masked

    def test_get_token_info_without_token(self):
        """get_token_info when no token → "None" preview."""
        auth = OAuth2Auth()
        info = auth.get_token_info()
        assert info["access_token"] == "None"
        assert info["has_refresh_token"] is False
        assert info["needs_refresh"] is True

    # ── Lines 226-239: _save_to_storage ───────────────────────────────────

    def test_save_to_storage_success(self):
        """Lines 226-237: tokens persisted to secure storage."""
        storage = MagicMock()
        storage.retrieve.return_value = None
        auth = OAuth2Auth(
            client_id="c",
            secure_storage=storage,
            storage_key="k",
        )
        auth.access_token = "at"
        auth.refresh_token = "rt"
        auth.expires_at = datetime(2099, 1, 1)
        auth._save_to_storage()
        storage.store.assert_called_once()

    def test_save_to_storage_no_storage(self):
        """Lines 222-223: no storage configured → skip."""
        auth = OAuth2Auth(client_id="c")
        auth.secure_storage = None
        auth._save_to_storage()  # should not raise

    def test_save_to_storage_failure(self):
        """Lines 238-239: storage.store raises — logged, not fatal."""
        storage = MagicMock()
        storage.retrieve.return_value = None
        storage.store.side_effect = RuntimeError("write error")
        auth = OAuth2Auth(
            client_id="c",
            secure_storage=storage,
            storage_key="k",
        )
        auth.access_token = "at"
        auth._save_to_storage()  # should not raise

    def test_save_to_storage_no_expires_at(self):
        """_save_to_storage when expires_at is None."""
        storage = MagicMock()
        storage.retrieve.return_value = None
        auth = OAuth2Auth(
            client_id="c",
            secure_storage=storage,
            storage_key="k",
        )
        auth.access_token = "at"
        auth.expires_at = None
        auth._save_to_storage()
        call_args = storage.store.call_args[0]
        saved = json.loads(call_args[1])
        assert saved["expires_at"] is None

    # ── Lines 246-250: _parse_expires_at edge cases ───────────────────────

    def test_parse_expires_at_invalid_string(self):
        """Lines 249-250: bad date string returns None."""
        assert OAuth2Auth._parse_expires_at("not-a-date") is None

    def test_parse_expires_at_none(self):
        """Line 244: None returns None."""
        assert OAuth2Auth._parse_expires_at(None) is None

    def test_parse_expires_at_valid_tz_aware(self):
        """Line 248: tz-aware ISO string → naive datetime."""
        result = OAuth2Auth._parse_expires_at("2099-01-01T00:00:00+00:00")
        assert result is not None
        assert result.tzinfo is None

    def test_parse_expires_at_valid_naive(self):
        """Line 248 branch: naive ISO string preserved."""
        result = OAuth2Auth._parse_expires_at("2099-01-01T12:00:00")
        assert result is not None
        assert result.tzinfo is None

    # ── Line 261: no token URL → raise ────────────────────────────────────

    def test_refresh_without_token_url_raises(self):
        """Line 261: AuthError when token_url is None."""
        auth = OAuth2Auth(client_id="c", client_secret="s", token_url=None)
        with pytest.raises(AuthError, match="No token URL"):
            auth._refresh_access_token()

    # ── Line 345: no refresh token or client credentials ──────────────────

    def test_build_grant_data_no_credentials_raises(self):
        """Line 345: AuthError when neither flow is possible."""
        auth = OAuth2Auth(token_url="https://t.test/tok")
        with pytest.raises(AuthError, match="No refresh token or client credentials"):
            auth._build_grant_data()

    # ── Lines 364-365: invalid token URL ──────────────────────────────────

    def test_post_token_request_invalid_url(self):
        """Lines 364-365: AuthError for structurally invalid token URL."""
        auth = OAuth2Auth(
            client_id="c",
            client_secret="s",
            token_url="ftp://not-http.example.com/token",
        )
        with pytest.raises(AuthError, match="Invalid token URL"):
            auth._post_token_request({"grant_type": "client_credentials"})

    # ── Lines 396-437: transport errors, retries, connection refused ──────

    @patch("equinox.auth.oauth2.httpx.Client")
    def test_http_status_error_no_retry(self, mock_client_class):
        """Lines 396-402: HTTPStatusError → immediate AuthError, no retry."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client

        resp = Mock()
        resp.status_code = 400
        mock_client.post.side_effect = httpx.HTTPStatusError(
            "Bad Request", request=Mock(), response=resp,
        )

        auth = OAuth2Auth(
            client_id="c",
            client_secret="s",
            token_url="https://auth.example.com/token",
        )
        with pytest.raises(AuthError, match="HTTP 400"):
            auth._refresh_access_token()

    @patch("equinox.auth.oauth2.time.sleep")
    @patch("equinox.auth.oauth2.httpx.Client")
    def test_transport_error_retries_then_fails(self, mock_client_class, mock_sleep):
        """Lines 403-437: transient TransportError retried then raises."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_client.post.side_effect = httpx.TransportError("network error")

        auth = OAuth2Auth(
            client_id="c",
            client_secret="s",
            token_url="https://auth.example.com/token",
        )
        with pytest.raises(AuthError, match="Failed to refresh"):
            auth._refresh_access_token()
        # Should have retried (3 attempts total, sleep called between)
        assert mock_sleep.call_count == 2  # 2 waits between 3 attempts

    @patch("equinox.auth.oauth2.httpx.Client")
    def test_connection_refused_skips_retries(self, mock_client_class):
        """Lines 407-419: ConnectError with 'connection refused' skips retries."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_client.post.side_effect = httpx.ConnectError(
            "[Errno 10061] connection refused"
        )

        auth = OAuth2Auth(
            client_id="c",
            client_secret="s",
            token_url="https://auth.example.com/token",
        )
        with pytest.raises(AuthError, match="Failed to refresh"):
            auth._refresh_access_token()
        # Only 1 attempt — no retries for connection refused
        assert mock_client.post.call_count == 1

    @patch("equinox.auth.oauth2.httpx.Client")
    def test_timeout_error_retries(self, mock_client_class):
        """TimeoutException is also retried."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client

        # First two calls timeout, third succeeds
        success_resp = Mock()
        success_resp.status_code = 200
        success_resp.json.return_value = {
            "access_token": "recovered-tok",
            "expires_in": 3600,
        }
        success_resp.raise_for_status = Mock()
        mock_client.post.side_effect = [
            httpx.TimeoutException("timeout 1"),
            httpx.TimeoutException("timeout 2"),
            success_resp,
        ]

        auth = OAuth2Auth(
            client_id="c",
            client_secret="s",
            token_url="https://auth.example.com/token",
        )
        with patch("equinox.auth.oauth2.time.sleep"):
            auth._refresh_access_token()
        assert auth.access_token == "recovered-tok"

    # ── Lines 450-454: invalid JSON from token endpoint ───────────────────

    @patch("equinox.auth.oauth2.httpx.Client")
    def test_apply_token_response_invalid_json(self, mock_client_class):
        """Lines 450-454: AuthError when token endpoint returns non-JSON."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client

        resp = Mock()
        resp.status_code = 200
        resp.json.side_effect = ValueError("not JSON")
        resp.raise_for_status = Mock()
        resp.text = "not json"
        resp.headers = {}
        mock_client.post.return_value = resp

        auth = OAuth2Auth(
            client_id="c",
            client_secret="s",
            token_url="https://auth.example.com/token",
        )
        with pytest.raises(AuthError, match="Invalid token endpoint response"):
            auth._refresh_access_token()

    # ── Line 458: no access_token in response ─────────────────────────────

    @patch("equinox.auth.oauth2.httpx.Client")
    def test_apply_token_response_missing_access_token(self, mock_client_class):
        """Line 458: AuthError when response body lacks access_token."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client

        resp = Mock()
        resp.status_code = 200
        resp.json.return_value = {"token_type": "Bearer"}  # no access_token
        resp.raise_for_status = Mock()
        resp.text = "{}"
        resp.headers = {}
        mock_client.post.return_value = resp

        auth = OAuth2Auth(
            client_id="c",
            client_secret="s",
            token_url="https://auth.example.com/token",
        )
        with pytest.raises(AuthError, match="did not return access_token"):
            auth._refresh_access_token()

    # ── Lines 492-493: invalid refresh token in response ──────────────────

    @patch("equinox.auth.oauth2.httpx.Client")
    def test_invalid_refresh_token_from_endpoint_kept_old(self, mock_client_class):
        """Lines 492-493: CRLF in refresh_token from server → keep old one."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client

        resp = Mock()
        resp.status_code = 200
        resp.json.return_value = {
            "access_token": "valid-tok",
            "expires_in": 3600,
            "refresh_token": "bad\r\ntoken",  # CRLF injection attempt
        }
        resp.raise_for_status = Mock()
        resp.text = ""
        resp.headers = {}
        mock_client.post.return_value = resp

        auth = OAuth2Auth(
            client_id="c",
            client_secret="s",
            token_url="https://auth.example.com/token",
            refresh_token="old-good-rt",
        )
        auth.apply(Mock(), {})
        assert auth.access_token == "valid-tok"
        assert auth.refresh_token == "old-good-rt"  # old kept

    # ── _capture_token_response edge cases (lines 279-283, 295-296, 300-301) ──

    def test_capture_token_response_non_json(self):
        """Lines 279-283: response.json() fails → fall back to raw text."""
        resp = Mock()
        resp.json.side_effect = ValueError("not JSON")
        resp.text = "plain error message"
        resp.headers = MagicMock()
        resp.headers.items.return_value = [("content-type", "text/plain")]
        resp.request = Mock()
        resp.request.url = "https://auth.example.com/token"
        resp.status_code = 200

        auth = OAuth2Auth(client_id="c")
        auth._capture_token_response(resp)
        assert auth.last_token_response is not None
        assert "_raw" in auth.last_token_response["body"]

    def test_capture_token_response_text_also_fails(self):
        """Lines 281-283: both json() and text fail → empty body."""
        resp = Mock()
        resp.json.side_effect = ValueError("no JSON")
        type(resp).text = PropertyMock(side_effect=RuntimeError("no text"))
        resp.headers = MagicMock()
        resp.headers.items.return_value = []
        resp.request = Mock()
        resp.request.url = "https://auth.example.com/token"
        resp.status_code = 200

        auth = OAuth2Auth(client_id="c")
        auth._capture_token_response(resp)
        assert auth.last_token_response["body"] == {}

    def test_capture_token_response_request_url_fails(self):
        """Lines 295-296: accessing response.request raises → fallback."""
        resp = Mock()
        resp.json.return_value = {"access_token": "short"}
        resp.headers = MagicMock()
        resp.headers.items.return_value = []
        type(resp).request = PropertyMock(side_effect=RuntimeError("no request"))
        resp.status_code = 200

        auth = OAuth2Auth(client_id="c", token_url="https://fallback.test/tok")
        auth._capture_token_response(resp)
        assert auth.last_token_response["url"] == "https://fallback.test/tok"

    def test_capture_token_response_status_code_fails(self):
        """Lines 300-301: accessing response.status_code raises → 0."""
        resp = Mock()
        resp.json.return_value = {"ok": True}
        resp.headers = MagicMock()
        resp.headers.items.return_value = []
        resp.request = Mock()
        resp.request.url = "https://auth.test/tok"
        type(resp).status_code = PropertyMock(side_effect=RuntimeError("no status"))

        auth = OAuth2Auth(client_id="c")
        auth._capture_token_response(resp)
        assert auth.last_token_response["status_code"] == 0

    def test_capture_token_response_headers_fail(self):
        """Lines 290-291: response.headers.items() raises → empty dict."""
        resp = Mock()
        resp.json.return_value = {"access_token": "tok123456789abc"}
        resp.headers = MagicMock()
        resp.headers.items.side_effect = RuntimeError("no headers")
        resp.request = Mock()
        resp.request.url = "https://auth.test/tok"
        resp.status_code = 200

        auth = OAuth2Auth(client_id="c")
        auth._capture_token_response(resp)
        assert auth.last_token_response["headers"] == {}

    def test_capture_token_response_redacts_long_tokens(self):
        """Lines 274-278: long token values are redacted in snapshot."""
        resp = Mock()
        resp.json.return_value = {
            "access_token": "abcdefghijklmnopqrstuvwxyz",
            "refresh_token": "1234567890abcdef1234567890",
            "id_token": "eyJhbGciOiJSUzI1NiJ9.payload.sig",
            "token_type": "Bearer",
            "expires_in": 3600,
        }
        resp.headers = MagicMock()
        resp.headers.items.return_value = [("content-type", "application/json")]
        resp.request = Mock()
        resp.request.url = "https://auth.test/tok"
        resp.status_code = 200

        auth = OAuth2Auth(client_id="c")
        auth._capture_token_response(resp)
        body = auth.last_token_response["body"]
        # Tokens should be redacted (first 8 + … + last 4)
        assert "…" in body["access_token"]
        assert body["token_type"] == "Bearer"  # non-token key not redacted

    def test_capture_token_response_short_token_not_redacted(self):
        """Lines 275-278: short tokens (<=12 chars) are NOT redacted."""
        resp = Mock()
        resp.json.return_value = {
            "access_token": "short",
            "token_type": "Bearer",
        }
        resp.headers = MagicMock()
        resp.headers.items.return_value = []
        resp.request = Mock()
        resp.request.url = "https://auth.test/tok"
        resp.status_code = 200

        auth = OAuth2Auth(client_id="c")
        auth._capture_token_response(resp)
        assert auth.last_token_response["body"]["access_token"] == "short"

    def test_capture_filters_set_cookie(self):
        """Lines 286-289: set-cookie header is filtered out."""
        resp = Mock()
        resp.json.return_value = {"ok": True}
        resp.headers = MagicMock()
        resp.headers.items.return_value = [
            ("content-type", "application/json"),
            ("set-cookie", "session=secret"),
        ]
        resp.request = Mock()
        resp.request.url = "https://auth.test/tok"
        resp.status_code = 200

        auth = OAuth2Auth(client_id="c")
        auth._capture_token_response(resp)
        assert "set-cookie" not in auth.last_token_response["headers"]
        assert "content-type" in auth.last_token_response["headers"]

    # ── _needs_refresh edge cases ─────────────────────────────────────────

    def test_needs_refresh_no_expiry(self):
        """Lines 173-176: no expires_at → don't refresh (reuse token)."""
        auth = OAuth2Auth(access_token="tok")
        auth.expires_at = None
        assert not auth._needs_refresh()

    # ── Proxy attribute ───────────────────────────────────────────────────

    @patch("equinox.auth.oauth2.httpx.Client")
    def test_proxy_passed_to_token_request(self, mock_client_class):
        """Line 384: proxy kwarg passed to httpx.Client."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        resp = Mock()
        resp.status_code = 200
        resp.json.return_value = {
            "access_token": "tok",
            "expires_in": 3600,
        }
        resp.raise_for_status = Mock()
        resp.text = ""
        resp.headers = {}
        mock_client.post.return_value = resp

        auth = OAuth2Auth(
            client_id="c",
            client_secret="s",
            token_url="https://auth.example.com/token",
        )
        auth._proxy = "http://proxy.local:8080"
        auth._refresh_access_token()
        # Verify proxy was passed
        call_kwargs = mock_client_class.call_args[1]
        assert call_kwargs["proxy"] == "http://proxy.local:8080"

    # ── repr ──────────────────────────────────────────────────────────────

    def test_repr_with_token(self):
        """Line 160: repr with access_token present."""
        auth = OAuth2Auth(client_id="c", access_token="tok")
        r = repr(auth)
        assert "present" in r
        assert "client_id=c" in r

    def test_repr_without_token(self):
        """Line 160: repr without access_token."""
        auth = OAuth2Auth(client_id="c")
        r = repr(auth)
        assert "None" in r

    # ── from_dict restores expires_at ─────────────────────────────────────

    def test_from_dict_restores_expires_at(self):
        """Line 156: from_dict restores expires_at from ISO string."""
        auth = OAuth2Auth(
            client_id="c",
            access_token="tok",
        )
        auth.expires_at = datetime(2099, 6, 15, 12, 0, 0)
        d = auth.to_dict()
        restored = OAuth2Auth.from_dict(d)
        assert restored.expires_at == datetime(2099, 6, 15, 12, 0, 0)

    # ── _capture_token_response with request=None ────────────────────────

    def test_capture_token_response_request_is_none(self):
        """Line 294: response.request is None → use self.token_url."""
        resp = Mock()
        resp.json.return_value = {"ok": True}
        resp.headers = MagicMock()
        resp.headers.items.return_value = []
        resp.request = None
        resp.status_code = 200

        auth = OAuth2Auth(client_id="c", token_url="https://my.tok/url")
        auth._capture_token_response(resp)
        assert auth.last_token_response["url"] == "https://my.tok/url"

    # ── token_url is None in capture fallback ─────────────────────────────

    def test_capture_url_fallback_when_token_url_none(self):
        """Line 296: response.request raises and token_url is None → empty string."""
        resp = Mock()
        resp.json.return_value = {"ok": True}
        resp.headers = MagicMock()
        resp.headers.items.return_value = []
        type(resp).request = PropertyMock(side_effect=RuntimeError("err"))
        resp.status_code = 200

        auth = OAuth2Auth(client_id="c", token_url=None)
        auth._capture_token_response(resp)
        assert auth.last_token_response["url"] == ""


# ── API Key repr coverage ─────────────────────────────────────────────────────


class TestAPIKeyReprCoverage:
    def test_repr_long_value(self):
        """Line 67: value > 4 chars shows first 4 + '...'."""
        auth = APIKeyAuth("X-Key", "longsecretvalue", "header")
        r = repr(auth)
        assert "long..." in r

    def test_repr_short_value(self):
        """Line 67: value <= 4 chars shows '***'."""
        auth = APIKeyAuth("X-Key", "tiny", "header")
        r = repr(auth)
        assert "***" in r

