"""100% coverage tests for equinox.auth.factory"""

import logging
import pytest
from unittest.mock import MagicMock, patch

from equinox.auth.factory import AUTH_REGISTRY, auth_from_dict
from equinox.auth.bearer import BearerAuth
from equinox.auth.basic import BasicAuth
from equinox.auth.api_key import APIKeyAuth
from equinox.auth.oauth2 import OAuth2Auth
from equinox.auth.aws_sigv4 import AWSSigV4Auth


# ---------------------------------------------------------------------------
# Sample data for each auth type
# ---------------------------------------------------------------------------

BEARER_DATA = {"token": "mytoken"}
BASIC_DATA = {"username": "user", "password": "pass"}
API_KEY_DATA = {"key": "X-Api-Key", "value": "secret", "location": "header"}
OAUTH2_DATA = {
    "access_token": "at",
    "token_url": "https://auth.example.com/token",
    "client_id": "cid",
    "client_secret": "cs",
    "scope": "read",
}
AWS_DATA = {
    "access_key": "AKID",
    "secret_key": "SECRET",
    "region": "us-east-1",
    "service": "execute-api",
}


# ---------------------------------------------------------------------------
# AUTH_REGISTRY completeness
# ---------------------------------------------------------------------------

class TestAuthRegistry:
    EXPECTED_KEYS = {
        # Short names
        "bearer", "basic", "api_key", "oauth2", "aws_sigv4",
        # Class names
        "BearerAuth", "BasicAuth", "APIKeyAuth", "OAuth2Auth", "AWSSigV4Auth",
    }

    def test_all_expected_keys_present(self):
        assert self.EXPECTED_KEYS.issubset(AUTH_REGISTRY.keys())

    def test_registry_values_are_callable(self):
        for key, loader in AUTH_REGISTRY.items():
            assert callable(loader), f"Loader for '{key}' is not callable"

    def test_short_and_class_name_resolve_to_same_class(self):
        pairs = [
            ("bearer", "BearerAuth"),
            ("basic", "BasicAuth"),
            ("api_key", "APIKeyAuth"),
            ("oauth2", "OAuth2Auth"),
            ("aws_sigv4", "AWSSigV4Auth"),
        ]
        for short, class_name in pairs:
            assert AUTH_REGISTRY[short]() is AUTH_REGISTRY[class_name](), (
                f"{short!r} and {class_name!r} should resolve to the same class"
            )


# ---------------------------------------------------------------------------
# Lazy import helpers (_get_* functions)
# ---------------------------------------------------------------------------

class TestLazyImportHelpers:
    """Exercise each private loader directly to guarantee import coverage."""

    def test_get_bearer_returns_bearer_auth(self):
        from equinox.auth.factory import _get_bearer
        assert _get_bearer() is BearerAuth

    def test_get_basic_returns_basic_auth(self):
        from equinox.auth.factory import _get_basic
        assert _get_basic() is BasicAuth

    def test_get_api_key_returns_api_key_auth(self):
        from equinox.auth.factory import _get_api_key
        assert _get_api_key() is APIKeyAuth

    def test_get_oauth2_returns_oauth2_auth(self):
        from equinox.auth.factory import _get_oauth2
        assert _get_oauth2() is OAuth2Auth

    def test_get_aws_sigv4_returns_aws_sigv4_auth(self):
        from equinox.auth.factory import _get_aws_sigv4
        assert _get_aws_sigv4() is AWSSigV4Auth


# ---------------------------------------------------------------------------
# auth_from_dict — happy path (short type names)
# ---------------------------------------------------------------------------

class TestAuthFromDictShortNames:
    def test_bearer(self):
        obj = auth_from_dict("bearer", BEARER_DATA)
        assert isinstance(obj, BearerAuth)

    def test_basic(self):
        obj = auth_from_dict("basic", BASIC_DATA)
        assert isinstance(obj, BasicAuth)

    def test_api_key(self):
        obj = auth_from_dict("api_key", API_KEY_DATA)
        assert isinstance(obj, APIKeyAuth)

    def test_oauth2(self):
        obj = auth_from_dict("oauth2", OAUTH2_DATA)
        assert isinstance(obj, OAuth2Auth)

    def test_aws_sigv4(self):
        obj = auth_from_dict("aws_sigv4", AWS_DATA)
        assert isinstance(obj, AWSSigV4Auth)


# ---------------------------------------------------------------------------
# auth_from_dict — happy path (class-name aliases)
# ---------------------------------------------------------------------------

class TestAuthFromDictClassNames:
    def test_bearer_class_name(self):
        obj = auth_from_dict("BearerAuth", BEARER_DATA)
        assert isinstance(obj, BearerAuth)

    def test_basic_class_name(self):
        obj = auth_from_dict("BasicAuth", BASIC_DATA)
        assert isinstance(obj, BasicAuth)

    def test_api_key_class_name(self):
        obj = auth_from_dict("APIKeyAuth", API_KEY_DATA)
        assert isinstance(obj, APIKeyAuth)

    def test_oauth2_class_name(self):
        obj = auth_from_dict("OAuth2Auth", OAUTH2_DATA)
        assert isinstance(obj, OAuth2Auth)

    def test_aws_sigv4_class_name(self):
        obj = auth_from_dict("AWSSigV4Auth", AWS_DATA)
        assert isinstance(obj, AWSSigV4Auth)


# ---------------------------------------------------------------------------
# auth_from_dict — round-trip fidelity
# ---------------------------------------------------------------------------

class TestAuthFromDictRoundTrip:
    def test_bearer_round_trip(self):
        obj = auth_from_dict("bearer", BEARER_DATA)
        assert obj.to_dict()["token"] == "mytoken"

    def test_basic_round_trip(self):
        obj = auth_from_dict("basic", BASIC_DATA)
        d = obj.to_dict()
        assert d["username"] == "user"
        assert d["password"] == "pass"

    def test_api_key_round_trip(self):
        obj = auth_from_dict("api_key", API_KEY_DATA)
        d = obj.to_dict()
        assert d["key"] == "X-Api-Key"
        assert d["value"] == "secret"
        assert d["location"] == "header"

    def test_oauth2_round_trip(self):
        obj = auth_from_dict("oauth2", OAUTH2_DATA)
        d = obj.to_dict()
        assert d["client_id"] == "cid"
        assert d["access_token"] == "at"

    def test_aws_sigv4_round_trip(self):
        obj = auth_from_dict("aws_sigv4", AWS_DATA)
        d = obj.to_dict()
        assert d["access_key"] == "AKID"
        assert d["region"] == "us-east-1"


# ---------------------------------------------------------------------------
# auth_from_dict — unknown type → ValueError + warning log
# ---------------------------------------------------------------------------

class TestAuthFromDictUnknownType:
    def test_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown auth type: bogus"):
            auth_from_dict("bogus", {})

    def test_raises_value_error_empty_string(self):
        with pytest.raises(ValueError, match="Unknown auth type"):
            auth_from_dict("", {})

    def test_logs_warning_for_unknown_type(self, caplog):
        with caplog.at_level(logging.WARNING, logger="equinox.auth.factory"):
            with pytest.raises(ValueError):
                auth_from_dict("not_a_type", {})
        assert "Unknown auth type" in caplog.text
        assert "not_a_type" in caplog.text


# ---------------------------------------------------------------------------
# auth_from_dict — from_dict raises → error log, returns None
# ---------------------------------------------------------------------------

class TestAuthFromDictFromDictFailure:
    def test_returns_none_when_from_dict_raises(self):
        with patch.dict(AUTH_REGISTRY, {"bad_type": lambda: _make_failing_class()}):
            result = auth_from_dict("bad_type", {})
        assert result is None

    def test_logs_error_when_from_dict_raises(self, caplog):
        with caplog.at_level(logging.ERROR, logger="equinox.auth.factory"):
            with patch.dict(AUTH_REGISTRY, {"bad_type": lambda: _make_failing_class()}):
                result = auth_from_dict("bad_type", {})
        assert result is None
        assert "Failed to reconstruct auth" in caplog.text
        assert "bad_type" in caplog.text

    def test_does_not_propagate_exception(self):
        """auth_from_dict must swallow from_dict errors and return None."""
        with patch.dict(AUTH_REGISTRY, {"err_type": lambda: _make_failing_class()}):
            # No exception should escape
            result = auth_from_dict("err_type", {})
        assert result is None


def _make_failing_class():
    """Return a dummy class whose from_dict always raises RuntimeError."""

    class FailingAuth:
        @classmethod
        def from_dict(cls, data):
            raise RuntimeError("intentional failure")

    return FailingAuth

