"""Security tests for the auth package.

Covers CRLF injection prevention, input validation, credential masking
in __repr__, robust from_dict deserialization, and OAuth2 edge cases.
"""

import pytest
from unittest.mock import Mock, MagicMock, patch
from datetime import datetime, timedelta, timezone

from equinox.auth import (
    BearerAuth, BasicAuth,
    APIKeyAuth, OAuth2Auth, AWSSigV4Auth
)
from equinox.auth._base import _validate_credential, _MAX_CREDENTIAL_LENGTH
from equinox.core.exceptions import AuthError


# ── _validate_credential helper ────────────────────────────────────────────────

class TestValidateCredential:
    """Tests for the shared credential validation function."""

    def test_rejects_empty_string(self):
        with pytest.raises(AuthError, match="non-empty string"):
            _validate_credential("", "field")

    def test_rejects_none(self):
        with pytest.raises(AuthError, match="non-empty string"):
            _validate_credential(None, "field")

    def test_rejects_non_string(self):
        with pytest.raises(AuthError, match="non-empty string"):
            _validate_credential(12345, "field")

    def test_rejects_crlf_cr(self):
        with pytest.raises(AuthError, match="CRLF"):
            _validate_credential("token\rinjection", "field")

    def test_rejects_crlf_lf(self):
        with pytest.raises(AuthError, match="CRLF"):
            _validate_credential("token\ninjection", "field")

    def test_rejects_crlf_pair(self):
        with pytest.raises(AuthError, match="CRLF"):
            _validate_credential("token\r\nX-Injected: evil", "field")

    def test_rejects_overly_long_string(self):
        with pytest.raises(AuthError, match="maximum length"):
            _validate_credential("x" * (_MAX_CREDENTIAL_LENGTH + 1), "field")

    def test_accepts_valid_string(self):
        result = _validate_credential("valid-token-123", "field")
        assert result == "valid-token-123"

    def test_accepts_max_length(self):
        val = "a" * _MAX_CREDENTIAL_LENGTH
        assert _validate_credential(val, "field") == val


# ── BearerAuth security ───────────────────────────────────────────────────────

class TestBearerAuthSecurity:
    """Security tests for BearerAuth."""

    def test_rejects_empty_token(self):
        with pytest.raises(AuthError):
            BearerAuth("")

    def test_rejects_crlf_in_token(self):
        with pytest.raises(AuthError, match="CRLF"):
            BearerAuth("token\r\nX-Injected: evil")

    def test_rejects_newline_in_token(self):
        with pytest.raises(AuthError, match="CRLF"):
            BearerAuth("token\nevil-header: value")

    def test_rejects_none_token(self):
        with pytest.raises(AuthError):
            BearerAuth(None)

    def test_from_dict_missing_key_raises_auth_error(self):
        with pytest.raises(AuthError, match="missing key"):
            BearerAuth.from_dict({})

    def test_from_dict_wrong_type_raises_auth_error(self):
        with pytest.raises(AuthError, match="missing key"):
            BearerAuth.from_dict({"typo": "value"})

    def test_repr_masks_token(self):
        auth = BearerAuth("supersecrettoken123")
        r = repr(auth)
        assert "supersecrettoken123" not in r


# ── BasicAuth security ─────────────────────────────────────────────────────────

class TestBasicAuthSecurity:
    """Security tests for BasicAuth."""

    def test_rejects_empty_username(self):
        with pytest.raises(AuthError):
            BasicAuth("", "password")

    def test_rejects_empty_password(self):
        with pytest.raises(AuthError):
            BasicAuth("user", "")

    def test_rejects_crlf_in_username(self):
        with pytest.raises(AuthError, match="CRLF"):
            BasicAuth("user\r\nevil", "password")

    def test_rejects_crlf_in_password(self):
        with pytest.raises(AuthError, match="CRLF"):
            BasicAuth("user", "pass\nword")

    def test_from_dict_missing_username(self):
        with pytest.raises(AuthError, match="missing key"):
            BasicAuth.from_dict({"password": "pass"})

    def test_from_dict_missing_password(self):
        with pytest.raises(AuthError, match="missing key"):
            BasicAuth.from_dict({"username": "user"})

    def test_repr_masks_username(self):
        auth = BasicAuth("administrator", "secret")
        r = repr(auth)
        assert "administrator" not in r
        assert "ad****" in r

    def test_repr_masks_short_username(self):
        auth = BasicAuth("ab", "secret")
        r = repr(auth)
        assert "****" in r


# ── APIKeyAuth security ───────────────────────────────────────────────────────

class TestAPIKeyAuthSecurity:
    """Security tests for APIKeyAuth."""

    def test_rejects_empty_key(self):
        with pytest.raises(AuthError):
            APIKeyAuth("", "value")

    def test_rejects_empty_value(self):
        with pytest.raises(AuthError):
            APIKeyAuth("X-API-Key", "")

    def test_rejects_crlf_in_key_name(self):
        with pytest.raises(AuthError, match="CRLF"):
            APIKeyAuth("Key\r\nInjected: evil", "value")

    def test_rejects_crlf_in_value(self):
        with pytest.raises(AuthError, match="CRLF"):
            APIKeyAuth("X-API-Key", "value\r\nInjected: evil")

    def test_from_dict_missing_key(self):
        with pytest.raises(AuthError, match="missing key"):
            APIKeyAuth.from_dict({"value": "v"})

    def test_from_dict_missing_value(self):
        with pytest.raises(AuthError, match="missing key"):
            APIKeyAuth.from_dict({"key": "k"})


# ── AWSSigV4Auth security ─────────────────────────────────────────────────────

class TestAWSSigV4AuthSecurity:
    """Security tests for AWSSigV4Auth."""

    def test_repr_masks_access_key(self):
        auth = AWSSigV4Auth("AKIAIOSFODNN7EXAMPLE", "secret", "us-east-1", "s3")
        r = repr(auth)
        assert "AKIAIOSFODNN7EXAMPLE" not in r
        assert "AKIA****" in r

    def test_repr_short_access_key_fully_masked(self):
        auth = AWSSigV4Auth("AK", "sec", "us-east-1", "s3")
        r = repr(auth)
        assert "AK" not in r or "****" in r

    def test_from_dict_missing_access_key(self):
        d = {"secret_key": "S", "region": "us-east-1", "service": "s3"}
        with pytest.raises(AuthError, match="missing key"):
            AWSSigV4Auth.from_dict(d)

    def test_from_dict_missing_secret_key(self):
        d = {"access_key": "K", "region": "us-east-1", "service": "s3"}
        with pytest.raises(AuthError, match="missing key"):
            AWSSigV4Auth.from_dict(d)

    def test_from_dict_missing_region(self):
        d = {"access_key": "K", "secret_key": "S", "service": "s3"}
        with pytest.raises(AuthError, match="missing key"):
            AWSSigV4Auth.from_dict(d)

    def test_from_dict_empty_dict(self):
        with pytest.raises(AuthError, match="missing key"):
            AWSSigV4Auth.from_dict({})


# ── OAuth2Auth security & robustness ──────────────────────────────────────────

class TestOAuth2AuthSecurity:
    """Security and robustness tests for OAuth2Auth."""

    def test_token_timeout_negative_clamped(self):
        auth = OAuth2Auth(client_id="c", client_secret="s", token_timeout=-5)
        assert auth.token_timeout == OAuth2Auth.DEFAULT_TOKEN_TIMEOUT

    def test_token_timeout_zero_clamped(self):
        auth = OAuth2Auth(client_id="c", client_secret="s", token_timeout=0)
        assert auth.token_timeout == OAuth2Auth.DEFAULT_TOKEN_TIMEOUT

    def test_token_timeout_very_large_clamped(self):
        auth = OAuth2Auth(client_id="c", client_secret="s", token_timeout=9999)
        assert auth.token_timeout == 300.0

    def test_token_timeout_valid_preserved(self):
        auth = OAuth2Auth(client_id="c", client_secret="s", token_timeout=15.0)
        assert auth.token_timeout == 15.0

    def test_storage_key_none_client_id_no_collision(self):
        """Two OAuth2 instances with client_id=None should get unique storage keys."""
        auth1 = OAuth2Auth()
        auth2 = OAuth2Auth()
        assert auth1.storage_key != auth2.storage_key
        assert auth1.storage_key.startswith("oauth2_anonymous_")
        assert auth2.storage_key.startswith("oauth2_anonymous_")

    def test_storage_key_with_client_id(self):
        auth = OAuth2Auth(client_id="my-client")
        assert auth.storage_key == "oauth2_my-client"

    def test_storage_key_explicit_overrides(self):
        auth = OAuth2Auth(client_id="c", storage_key="custom-key")
        assert auth.storage_key == "custom-key"

    @patch("equinox.auth._oauth2.httpx.Client")
    def test_expires_in_float_string_handled(self, mock_client_class):
        """Token endpoint returning expires_in as '3600.5' should not crash."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "access_token": "tok",
            "expires_in": "3600.5",
        }
        mock_client.post.return_value = mock_resp

        auth = OAuth2Auth(
            client_id="c",
            client_secret="s",
            token_url="https://auth.example.com/token",
        )
        headers = {}
        auth.apply(Mock(), headers)
        assert auth.access_token == "tok"
        assert auth.expires_at is not None

    @patch("equinox.auth._oauth2.httpx.Client")
    def test_expires_in_non_numeric_uses_default(self, mock_client_class):
        """Token endpoint returning expires_in as 'invalid' should use default."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "access_token": "tok",
            "expires_in": "not-a-number",
        }
        mock_client.post.return_value = mock_resp

        auth = OAuth2Auth(
            client_id="c",
            client_secret="s",
            token_url="https://auth.example.com/token",
        )
        headers = {}
        auth.apply(Mock(), headers)
        assert auth.access_token == "tok"
        assert auth.expires_at is not None

    @patch("equinox.auth._oauth2.httpx.Client")
    def test_expires_in_negative_uses_default(self, mock_client_class):
        """Token endpoint returning negative expires_in should use default."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "access_token": "tok",
            "expires_in": -100,
        }
        mock_client.post.return_value = mock_resp

        auth = OAuth2Auth(
            client_id="c",
            client_secret="s",
            token_url="https://auth.example.com/token",
        )
        headers = {}
        auth.apply(Mock(), headers)
        assert auth.access_token == "tok"

    @patch("equinox.auth._oauth2.httpx.Client")
    def test_crlf_in_access_token_from_server_rejected(self, mock_client_class):
        """Access token with CRLF from a malicious server must be rejected."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "access_token": "evil\r\nX-Injected: header",
            "expires_in": 3600,
        }
        mock_client.post.return_value = mock_resp

        auth = OAuth2Auth(
            client_id="c",
            client_secret="s",
            token_url="https://auth.example.com/token",
        )
        with pytest.raises(AuthError, match="CRLF"):
            auth.apply(Mock(), {})


# ── Package __init__ exports ──────────────────────────────────────────────────

class TestPackageExports:
    """Verify all auth classes are accessible from the package."""

    def test_aws_sigv4_exported(self):
        from equinox.auth import AWSSigV4Auth
        assert AWSSigV4Auth is not None

    def test_all_strategies_in_all(self):
        import equinox.auth as auth_pkg
        for name in ["AuthStrategy", "BearerAuth", "APIKeyAuth",
                      "BasicAuth", "OAuth2Auth", "AWSSigV4Auth"]:
            assert name in auth_pkg.__all__

