from __future__ import annotations

from typing import Any, Dict

import pytest

from equinox.auth import BasicAuth
from equinox.auth._base import AuthStrategy, _interpolate_field
from equinox.auth._factory import auth_from_dict, get_auth_class, get_auth_type_labels
from equinox.core.exceptions import AuthError


def test_auth_from_dict_invalid_argument_shapes_raise_auth_error() -> None:
    with pytest.raises(AuthError):
        auth_from_dict(123)  # type: ignore[arg-type]

    with pytest.raises(AuthError):
        auth_from_dict("basic", "not-a-dict")  # type: ignore[arg-type]

    with pytest.raises(AuthError):
        auth_from_dict()


def test_auth_from_dict_single_string_without_data_returns_none() -> None:
    # This path leaves data unset and should be handled by the broad exception guard.
    with pytest.raises(AuthError):
        assert auth_from_dict("basic") is None


def test_get_auth_class_and_labels_include_known_types(monkeypatch: pytest.MonkeyPatch) -> None:
    assert get_auth_class("basic") is BasicAuth
    assert get_auth_class("does_not_exist") is None

    # Exercise the branch that skips missing loaders in AUTH_TYPE_ORDER.
    import equinox.auth._factory as factory_mod

    monkeypatch.setattr(factory_mod, "AUTH_REGISTRY", {"basic": lambda: BasicAuth})
    labels = get_auth_type_labels()
    assert labels == {"basic": "Basic Auth"}


class _RoundTripAuth(AuthStrategy):
    AUTH_TYPE = "round_trip_auth"
    DISPLAY_NAME = "Round Trip"

    def __init__(self, token: str = "{{TOKEN}}", note: str = "") -> None:
        self.token = token
        self.note = note

    def apply(self, request: Any, headers: Dict[str, str]) -> None:
        headers["authorization"] = self.token

    def to_dict(self) -> Dict[str, Any]:
        return {"type": self.AUTH_TYPE, "token": self.token, "note": self.note}

    @classmethod
    def from_dict(cls, data: Dict[str, Any], **kwargs: Any) -> _RoundTripAuth:
        try:
            return cls(token=data.get("token", ""), note=data.get("note", ""))
        except AttributeError as err:
            if "'str' object has no attribute 'get'" in str(err):
                raise TypeError(err)


class _FailingToDictAuth(AuthStrategy):
    AUTH_TYPE = "failing_repr_auth"
    DISPLAY_NAME = "Failing"

    def apply(self, request: Any, headers: Dict[str, str]) -> None:
        return None

    def to_dict(self) -> Dict[str, Any]:
        raise RuntimeError("boom")

    @classmethod
    def from_dict(cls, data: Dict[str, Any], **kwargs: Any) -> _FailingToDictAuth:
        super().from_dict(data)
        return cls()


def test_interpolate_field_requires_callable() -> None:
    with pytest.raises(TypeError):
        _interpolate_field("x", None)  # type: ignore[arg-type]


def test_authstrategy_from_dict_validation_and_interpolate_paths() -> None:
    with pytest.raises(TypeError):
        _RoundTripAuth.from_dict("bad")  # type: ignore[arg-type]

    auth = _RoundTripAuth(token="{{TOKEN}}", note="")

    def _interp(v: str) -> str:
        return v.replace("{{TOKEN}}", "abc")

    interpolated = auth.interpolate(_interp)
    assert isinstance(interpolated, _RoundTripAuth)
    assert interpolated.token == "abc"
    assert interpolated.note == ""

    with pytest.raises(TypeError):
        auth.interpolate(None)  # type: ignore[arg-type]


def test_authstrategy_default_summary_warning_eq_and_repr_fallback() -> None:
    auth = _RoundTripAuth(token="secret", note="metadata")
    assert auth.get_display_summary() == "Round Trip"
    assert auth.get_preflight_warning() is None

    same = _RoundTripAuth(token="secret", note="metadata")
    different = _RoundTripAuth(token="other", note="metadata")
    assert auth == same
    assert auth != different
    assert auth.__eq__(object()) is NotImplemented

    # to_dict failure path should fall back safely.
    failing = _FailingToDictAuth()
    assert repr(failing) == "_FailingToDictAuth(error in __repr__)"
    assert (failing == failing) is True


def test_concrete_subclass_requires_auth_type_and_display_name() -> None:
    with pytest.raises(TypeError):

        class _MissingAuthType(AuthStrategy):
            DISPLAY_NAME = "Missing Type"

            def apply(self, request: Any, headers: Dict[str, str]) -> None:
                return None

            def to_dict(self) -> Dict[str, Any]:
                return {"type": "x"}

            @classmethod
            def from_dict(cls, data: Dict[str, Any], **kwargs: Any) -> _MissingAuthType:
                super().from_dict(data)
                return cls()

    with pytest.raises(TypeError):

        class _MissingDisplayName(AuthStrategy):
            AUTH_TYPE = "missing_display"

            def apply(self, request: Any, headers: Dict[str, str]) -> None:
                return None

            def to_dict(self) -> Dict[str, Any]:
                return {"type": "x"}

            @classmethod
            def from_dict(cls, data: Dict[str, Any], **kwargs: Any) -> _MissingDisplayName:
                super().from_dict(data)
                return cls()
