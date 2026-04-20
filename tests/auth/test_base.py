"""100% coverage tests for equinox.auth.base"""

import pytest
from typing import Any, Dict

from equinox.auth.base import AuthStrategy, _MAX_CREDENTIAL_LENGTH, _validate_credential
from equinox.core.exceptions import AuthError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class ConcreteAuth(AuthStrategy):
    """Minimal concrete implementation used by tests."""

    AUTH_TYPE = "test_concrete_auth_base"
    DISPLAY_NAME = "Test Concrete Auth Base"

    def __init__(self, token: str = "tok") -> None:
        self.token = token

    def apply(self, request: Any, headers: Dict[str, str]) -> None:
        headers["Authorization"] = f"Bearer {self.token}"

    def to_dict(self) -> Dict[str, Any]:
        return {"type": "concrete", "token": self.token}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ConcreteAuth":
        return cls(token=data["token"])


# ---------------------------------------------------------------------------
# _validate_credential — happy path
# ---------------------------------------------------------------------------

class TestValidateCredentialValid:
    def test_returns_value_unchanged(self):
        result = _validate_credential("mysecret", "token")
        assert result == "mysecret"

    def test_single_character(self):
        assert _validate_credential("x", "key") == "x"

    def test_exactly_max_length(self):
        value = "a" * _MAX_CREDENTIAL_LENGTH
        assert _validate_credential(value, "field") == value

    def test_field_name_appears_in_no_error_when_valid(self):
        # Should not raise regardless of field_name content
        assert _validate_credential("valid", "my field") == "valid"


# ---------------------------------------------------------------------------
# _validate_credential — non-string / empty
# ---------------------------------------------------------------------------

class TestValidateCredentialNotStringOrEmpty:
    @pytest.mark.parametrize("bad_value", [
        None,
        42,
        3.14,
        [],
        {},
        b"bytes",
        True,
    ])
    def test_non_string_raises(self, bad_value):
        with pytest.raises(AuthError, match="must be a non-empty string"):
            _validate_credential(bad_value, "field")  # type: ignore[arg-type]

    def test_empty_string_raises(self):
        with pytest.raises(AuthError, match="must be a non-empty string"):
            _validate_credential("", "token")

    def test_error_message_contains_field_name(self):
        with pytest.raises(AuthError) as exc_info:
            _validate_credential("", "my_token")
        assert "my_token" in str(exc_info.value)


# ---------------------------------------------------------------------------
# _validate_credential — length
# ---------------------------------------------------------------------------

class TestValidateCredentialLength:
    def test_one_over_max_raises(self):
        value = "a" * (_MAX_CREDENTIAL_LENGTH + 1)
        with pytest.raises(AuthError, match="exceeds maximum length"):
            _validate_credential(value, "secret")

    def test_error_message_contains_max_length(self):
        value = "b" * (_MAX_CREDENTIAL_LENGTH + 1)
        with pytest.raises(AuthError) as exc_info:
            _validate_credential(value, "tok")
        assert str(_MAX_CREDENTIAL_LENGTH) in str(exc_info.value)

    def test_error_message_contains_field_name_on_length(self):
        value = "c" * (_MAX_CREDENTIAL_LENGTH + 1)
        with pytest.raises(AuthError) as exc_info:
            _validate_credential(value, "api_key")
        assert "api_key" in str(exc_info.value)


# ---------------------------------------------------------------------------
# _validate_credential — CRLF injection
# ---------------------------------------------------------------------------

class TestValidateCredentialCRLF:
    def test_carriage_return_raises(self):
        with pytest.raises(AuthError, match="CRLF injection"):
            _validate_credential("tok\ren", "header")

    def test_newline_raises(self):
        with pytest.raises(AuthError, match="CRLF injection"):
            _validate_credential("tok\nen", "header")

    def test_crlf_combined_raises(self):
        with pytest.raises(AuthError, match="CRLF injection"):
            _validate_credential("tok\r\nen", "header")

    def test_error_message_contains_field_name_on_crlf(self):
        with pytest.raises(AuthError) as exc_info:
            _validate_credential("bad\nvalue", "Authorization")
        assert "Authorization" in str(exc_info.value)

    def test_newline_at_start(self):
        with pytest.raises(AuthError, match="CRLF injection"):
            _validate_credential("\nsecret", "token")

    def test_newline_at_end(self):
        with pytest.raises(AuthError, match="CRLF injection"):
            _validate_credential("secret\n", "token")

    def test_cr_at_start(self):
        with pytest.raises(AuthError, match="CRLF injection"):
            _validate_credential("\rsecret", "token")


# ---------------------------------------------------------------------------
# AuthStrategy ABC
# ---------------------------------------------------------------------------

class TestAuthStrategyABC:
    def test_cannot_instantiate_abstract_class(self):
        with pytest.raises(TypeError):
            AuthStrategy()  # type: ignore[abstract]

    def test_subclass_missing_apply_raises_on_instantiation(self):
        class MissingApply(AuthStrategy):
            AUTH_TYPE = "test_missing_apply"
            DISPLAY_NAME = "Test Missing Apply"

            def to_dict(self):
                return {}

            @classmethod
            def from_dict(cls, data):
                return cls()

        with pytest.raises(TypeError):
            MissingApply()

    def test_subclass_missing_to_dict_raises_on_instantiation(self):
        class MissingToDict(AuthStrategy):
            AUTH_TYPE = "test_missing_to_dict"
            DISPLAY_NAME = "Test Missing To Dict"

            def apply(self, request, headers):
                pass

            @classmethod
            def from_dict(cls, data):
                return cls()

        with pytest.raises(TypeError):
            MissingToDict()

    def test_subclass_missing_from_dict_raises_on_instantiation(self):
        class MissingFromDict(AuthStrategy):
            AUTH_TYPE = "test_missing_from_dict"
            DISPLAY_NAME = "Test Missing From Dict"

            def apply(self, request, headers):
                pass

            def to_dict(self):
                return {}

        with pytest.raises(TypeError):
            MissingFromDict()


# ---------------------------------------------------------------------------
# ConcreteAuth — verifying the interface contract
# ---------------------------------------------------------------------------

class TestConcreteAuth:
    def test_instantiation(self):
        auth = ConcreteAuth("mytoken")
        assert auth.token == "mytoken"

    def test_apply_sets_header(self):
        auth = ConcreteAuth("abc123")
        headers: Dict[str, str] = {}
        auth.apply(request=None, headers=headers)
        assert headers["Authorization"] == "Bearer abc123"

    def test_to_dict(self):
        auth = ConcreteAuth("tok42")
        d = auth.to_dict()
        assert d == {"type": "concrete", "token": "tok42"}

    def test_from_dict_round_trip(self):
        original = ConcreteAuth("round-trip-token")
        restored = ConcreteAuth.from_dict(original.to_dict())
        assert restored.token == original.token

    def test_from_dict_returns_correct_type(self):
        auth = ConcreteAuth.from_dict({"token": "x"})
        assert isinstance(auth, ConcreteAuth)
        assert isinstance(auth, AuthStrategy)

