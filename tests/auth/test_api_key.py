"""100% coverage tests for equinox.auth.api_key."""

from __future__ import annotations

import logging
from unittest.mock import Mock

import pytest

from equinox.auth._api_key import _VALID_LOCATIONS, APIKeyAuth
from equinox.auth._base import _MAX_CREDENTIAL_LENGTH
from equinox.core.exceptions import AuthError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make(key="X-Api-Key", value="secret-value", location="header") -> APIKeyAuth:
    return APIKeyAuth(key=key, value=value, location=location)


def _request_with_params(params) -> Mock:
    """Mock request whose .params attribute is *params*."""
    req = Mock()
    req.params = params
    return req


def _request_without_params() -> Mock:
    """Mock request that has no .params attribute at all."""
    return Mock(spec=[])  # empty spec → no attributes


# ---------------------------------------------------------------------------
# __init__ — location validation
# ---------------------------------------------------------------------------


class TestAPIKeyAuthInit:
    def test_header_location_accepted(self):
        auth = APIKeyAuth(key="X-Api-Key", value="s3cr3t", location="header")
        assert auth.location == "header"

    def test_query_location_accepted(self):
        auth = APIKeyAuth(key="api_key", value="s3cr3t", location="query")
        assert auth.location == "query"

    def test_invalid_location_raises(self):
        with pytest.raises(AuthError, match="Invalid location"):
            APIKeyAuth(key="X-Api-Key", value="s3cr3t", location="cookie")

    def test_invalid_location_message_lists_valid_options(self):
        with pytest.raises(AuthError, match="header"):
            APIKeyAuth(key="k", value="v", location="body")

    # -- key validation via _validate_credential ----------------------------

    def test_empty_key_raises(self):
        with pytest.raises(AuthError, match="API key name"):
            APIKeyAuth(key="", value="v", location="header")

    def test_non_string_key_raises(self):
        with pytest.raises(AuthError, match="API key name"):
            APIKeyAuth(key=None, value="v", location="header")  # type: ignore[arg-type]

    def test_key_with_cr_raises(self):
        with pytest.raises(AuthError, match="CRLF"):
            APIKeyAuth(key="X-Api-Key\r", value="v", location="header")

    def test_key_with_lf_raises(self):
        with pytest.raises(AuthError, match="CRLF"):
            APIKeyAuth(key="X-Api-Key\n", value="v", location="header")

    def test_key_too_long_raises(self):
        with pytest.raises(AuthError, match="exceeds maximum length"):
            APIKeyAuth(key="k" * (_MAX_CREDENTIAL_LENGTH + 1), value="v", location="header")

    def test_key_at_max_length_accepted(self):
        auth = APIKeyAuth(key="k" * _MAX_CREDENTIAL_LENGTH, value="v", location="header")
        assert len(auth.key) == _MAX_CREDENTIAL_LENGTH

    # -- value validation via _validate_credential --------------------------

    def test_empty_value_raises(self):
        with pytest.raises(AuthError, match="API key value"):
            APIKeyAuth(key="k", value="", location="header")

    def test_non_string_value_raises(self):
        with pytest.raises(AuthError, match="API key value"):
            APIKeyAuth(key="k", value=123, location="header")  # type: ignore[arg-type]

    def test_value_with_cr_raises(self):
        with pytest.raises(AuthError, match="CRLF"):
            APIKeyAuth(key="k", value="secret\r", location="header")

    def test_value_with_lf_raises(self):
        with pytest.raises(AuthError, match="CRLF"):
            APIKeyAuth(key="k", value="secret\n", location="header")

    def test_value_too_long_raises(self):
        with pytest.raises(AuthError, match="exceeds maximum length"):
            APIKeyAuth(key="k", value="v" * (_MAX_CREDENTIAL_LENGTH + 1), location="header")

    def test_attributes_stored_correctly(self):
        auth = APIKeyAuth(key="My-Key", value="my-val", location="query")
        assert auth.key == "My-Key"
        assert auth.value == "my-val"
        assert auth.location == "query"

    def test_valid_locations_constant(self):
        assert "header" in _VALID_LOCATIONS
        assert "query" in _VALID_LOCATIONS


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------


class TestAPIKeyAuthApply:
    def test_header_location_injects_into_headers(self):
        auth = _make(key="X-Api-Key", value="tok123", location="header")
        headers: dict = {}
        req = _request_with_params({})
        auth.apply(req, headers)
        assert headers == {"X-Api-Key": "tok123"}

    def test_header_location_does_not_touch_params(self):
        auth = _make(location="header")
        req = _request_with_params({"existing": "param"})
        headers: dict = {}
        auth.apply(req, headers)
        assert req.params == {"existing": "param"}

    def test_query_location_adds_to_existing_params(self):
        auth = _make(key="api_key", value="abc", location="query")
        req = _request_with_params({"other": "1"})
        headers: dict = {}
        auth.apply(req, headers)
        assert req.params["api_key"] == "abc"
        assert req.params["other"] == "1"  # existing params preserved
        assert headers == {}  # headers untouched

    def test_query_location_creates_params_when_none(self):
        """request.params is None → should be initialised to {}."""
        auth = _make(key="api_key", value="abc", location="query")
        req = _request_with_params(None)
        auth.apply(req, {})
        assert req.params == {"api_key": "abc"}

    def test_query_location_creates_params_when_attribute_absent(self):
        """request has no .params attribute → should be created."""
        auth = _make(key="api_key", value="abc", location="query")
        req = _request_without_params()
        auth.apply(req, {})
        assert req.params == {"api_key": "abc"}

    def test_header_location_logged(self, caplog):
        auth = _make(key="X-Api-Key", value="tok", location="header")
        with caplog.at_level(logging.DEBUG, logger="equinox.auth._api_key"):
            auth.apply(_request_with_params({}), {})
        print(caplog.records)
        assert any("header" in r.message for r in caplog.records)

    def test_query_location_logged(self, caplog):
        auth = _make(key="api_key", value="tok", location="query")
        req = _request_with_params({})
        with caplog.at_level(logging.DEBUG, logger="equinox.auth._api_key"):
            auth.apply(req, {})
        assert any("query" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# to_dict / from_dict
# ---------------------------------------------------------------------------


class TestAPIKeyAuthSerialization:
    def test_to_dict_header(self):
        auth = _make(key="X-Api-Key", value="tok", location="header")
        d = auth.to_dict()
        assert d == {
            "type": "api_key",
            "key": "X-Api-Key",
            "value": "tok",
            "location": "header",
        }

    def test_to_dict_query(self):
        auth = _make(key="api_key", value="tok", location="query")
        d = auth.to_dict()
        assert d["location"] == "query"
        assert d["type"] == "api_key"

    def test_round_trip_header(self):
        original = _make(key="X-Key", value="v123", location="header")
        restored = APIKeyAuth.from_dict(original.to_dict())
        assert original == restored

    def test_round_trip_query(self):
        original = _make(key="q_key", value="qval", location="query")
        restored = APIKeyAuth.from_dict(original.to_dict())
        assert original == restored

    def test_from_dict_default_location_is_header(self):
        auth = APIKeyAuth.from_dict({"key": "k", "value": "v"})
        assert auth.location == "header"

    def test_from_dict_explicit_query_location(self):
        auth = APIKeyAuth.from_dict({"key": "k", "value": "v", "location": "query"})
        assert auth.location == "query"

    def test_from_dict_missing_key_raises_auth_error(self):
        with pytest.raises(AuthError, match="missing key"):
            APIKeyAuth.from_dict({"value": "v"})

    def test_from_dict_missing_value_raises_auth_error(self):
        with pytest.raises(AuthError, match="missing key"):
            APIKeyAuth.from_dict({"key": "k"})

    def test_from_dict_error_message_includes_class_name(self):
        with pytest.raises(AuthError, match="APIKeyAuth"):
            APIKeyAuth.from_dict({})

    def test_auth_type_constant(self):
        assert APIKeyAuth.AUTH_TYPE == "api_key"


# ---------------------------------------------------------------------------
# __eq__ and __hash__
# ---------------------------------------------------------------------------


class TestAPIKeyAuthEquality:
    def test_equal_to_itself(self):
        auth = _make()
        assert auth == auth

    def test_equal_instances(self):
        assert _make() == _make()

    def test_not_equal_different_key(self):
        assert _make(key="A") != _make(key="B")

    def test_not_equal_different_value(self):
        assert _make(value="a") != _make(value="b")

    def test_not_equal_different_location(self):
        assert _make(location="header") != _make(location="query")

    def test_not_equal_to_none(self):
        assert _make() != None  # noqa: E711

    def test_not_equal_returns_not_implemented_for_unknown_type(self):
        """__eq__ must return NotImplemented for non-APIKeyAuth objects."""
        auth = _make()
        result = auth.__eq__("not-an-auth-object")
        assert result is NotImplemented

    def test_hashable(self):
        auth = _make()
        assert isinstance(hash(auth), int)

    def test_can_be_used_in_set(self):
        a1 = _make()
        a2 = _make()
        s = {a1, a2}
        assert len(s) == 1

    def test_different_objects_produce_different_hashes(self):
        assert hash(_make(key="A")) != hash(_make(key="B"))

    def test_equal_objects_have_equal_hashes(self):
        assert hash(_make()) == hash(_make())

    def test_can_be_used_as_dict_key(self):
        auth = _make()
        d = {auth: "value"}
        assert d[_make()] == "value"


# ---------------------------------------------------------------------------
# __repr__
# ---------------------------------------------------------------------------


class TestAPIKeyAuthRepr:
    def test_repr_long_value_is_masked(self):
        """Value > 4 chars → show first 4 chars + '...'."""
        auth = APIKeyAuth(key="X-Api-Key", value="abcde", location="header")
        r = repr(auth)
        assert "abcd..." in r
        assert "abcde" not in r  # full value must not appear

    def test_repr_exactly_five_chars_masked(self):
        auth = APIKeyAuth(key="k", value="12345", location="header")
        assert "1234..." in repr(auth)

    def test_repr_exactly_four_chars_uses_stars(self):
        """Value == 4 chars → condition is False → '***'."""
        auth = APIKeyAuth(key="k", value="abcd", location="header")
        assert "***" in repr(auth)
        assert "abcd" not in repr(auth)

    def test_repr_short_value_uses_stars(self):
        """Value < 4 chars → '***'."""
        auth = APIKeyAuth(key="k", value="ab", location="header")
        assert "***" in repr(auth)

    def test_repr_single_char_value_uses_stars(self):
        auth = APIKeyAuth(key="k", value="x", location="header")
        assert "***" in repr(auth)

    def test_repr_contains_key(self):
        auth = _make(key="My-Header")
        assert "My-Header" in repr(auth)

    def test_repr_contains_location(self):
        auth = _make(location="query")
        assert "query" in repr(auth)

    def test_repr_format(self):
        auth = APIKeyAuth(key="K", value="vvvvv", location="header")
        r = repr(auth)
        assert r.startswith("APIKeyAuth(")
        assert "key='K'" in r
        assert "location='header'" in r
