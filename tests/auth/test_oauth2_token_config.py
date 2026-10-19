"""Tests for configurable OAuth2 token-endpoint behaviour.

Covers the four configuration fields added to support non-standard token
services (e.g. D&B Risk Analytics, which returns its token in a ``JWTToken``
field, expects a JSON body, and requires an extra Authorization header on the
token request itself):

- ``token_field_name``       — response JSON key holding the access token
- ``token_body_format``      — form-encoded (default) vs JSON token POST body
- ``token_request_headers``  — extra headers merged into the token POST
- ``default_token_ttl_seconds`` — fallback TTL when the response has no
  ``expires_in``
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import httpx
import pytest

from equinox.auth._oauth2 import OAuth2Auth
from equinox.auth._oauth2.operations import execute_token_post
from equinox.core.exceptions import AuthError
from equinox.core.util.time import utc_now


def _ok_response(payload: dict[str, Any], url: str = "https://example.com/token") -> httpx.Response:
    request = httpx.Request("POST", url)
    return httpx.Response(200, request=request, json=payload)


def _request() -> Any:
    return object()


# --------------------------------------------------------------------------- #
# token_field_name
# --------------------------------------------------------------------------- #


class TestTokenFieldName:
    def test_apply_extracts_token_from_custom_field(self, monkeypatch) -> None:
        auth = OAuth2Auth(
            token_url="https://integration.example.com/authenticate",
            client_id="cid",
            client_secret="csecret",
            token_field_name="JWTToken",
        )
        # Patch the request wrapper (above URL validation) so the canned
        # response flows through the real apply_token_response logic.
        monkeypatch.setattr(
            OAuth2Auth,
            "_post_token_request",
            lambda self, grant_data, **_: _ok_response({"JWTToken": "jwt-value"}),
        )

        headers: dict[str, str] = {}
        auth.apply_with_context(_request(), headers)

        assert auth.access_token == "jwt-value"
        assert headers["Authorization"] == "Bearer jwt-value"

    def test_missing_custom_field_raises_with_field_name(self, monkeypatch) -> None:
        auth = OAuth2Auth(
            token_url="https://integration.example.com/authenticate",
            client_id="cid",
            client_secret="csecret",
            token_field_name="JWTToken",
        )
        monkeypatch.setattr(
            OAuth2Auth,
            "_post_token_request",
            lambda self, grant_data, **_: _ok_response({"access_token": "wrong-field"}),
        )

        with pytest.raises(AuthError, match="JWTToken"):
            auth.apply_with_context(_request(), {})

    def test_default_field_name_is_access_token(self) -> None:
        auth = OAuth2Auth(token_url="https://example.com/token", client_id="c", client_secret="s")
        assert auth.token_field_name == "access_token"


# --------------------------------------------------------------------------- #
# token_body_format
# --------------------------------------------------------------------------- #


class _FakeClient:
    """Minimal httpx.Client stand-in capturing POST kwargs."""

    last_call: dict[str, Any] = {}
    response: httpx.Response = _ok_response({"access_token": "t"})

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        _FakeClient.last_call = {"url": url, **kwargs}
        return _FakeClient.response


class TestTokenBodyFormat:
    def test_json_format_sends_json_body(self, monkeypatch) -> None:
        auth = OAuth2Auth(
            token_url="https://example.com/token",
            client_id="cid",
            client_secret="csecret",
            token_body_format="json",
        )
        with patch("equinox.auth._oauth2.operations.httpx.Client", _FakeClient):
            response = execute_token_post(
                auth,
                {"grant_type": "client_credentials", "client_id": "cid", "client_secret": "cs"},
            )
        assert response.status_code == 200
        assert "json" in _FakeClient.last_call
        assert "data" not in _FakeClient.last_call
        assert _FakeClient.last_call["json"]["client_id"] == "cid"

    def test_default_format_sends_form_body(self, monkeypatch) -> None:
        auth = OAuth2Auth(
            token_url="https://example.com/token",
            client_id="cid",
            client_secret="csecret",
        )
        with patch("equinox.auth._oauth2.operations.httpx.Client", _FakeClient):
            execute_token_post(
                auth,
                {"grant_type": "client_credentials", "client_id": "cid", "client_secret": "cs"},
            )
        assert "data" in _FakeClient.last_call
        assert "json" not in _FakeClient.last_call


# --------------------------------------------------------------------------- #
# token_request_headers
# --------------------------------------------------------------------------- #


class TestTokenRequestHeaders:
    def test_custom_headers_merged_into_token_post(self, monkeypatch) -> None:
        auth = OAuth2Auth(
            token_url="https://example.com/token",
            client_id="cid",
            client_secret="csecret",
            token_request_headers={"Authorization": "Bearer api-key", "X-Custom": "v"},
        )
        with patch("equinox.auth._oauth2.operations.httpx.Client", _FakeClient):
            execute_token_post(
                auth,
                {"grant_type": "client_credentials", "client_id": "cid", "client_secret": "cs"},
            )
        headers = _FakeClient.last_call["headers"]
        assert headers["Authorization"] == "Bearer api-key"
        assert headers["X-Custom"] == "v"

    def test_custom_headers_override_basic_auth(self, monkeypatch) -> None:
        auth = OAuth2Auth(
            token_url="https://example.com/token",
            client_id="cid",
            client_secret="csecret",
            token_auth="basic",
            token_request_headers={"Authorization": "Bearer api-key"},
        )
        with patch("equinox.auth._oauth2.operations.httpx.Client", _FakeClient):
            execute_token_post(
                auth,
                {"grant_type": "client_credentials", "client_id": "cid", "client_secret": "cs"},
            )
        assert _FakeClient.last_call["headers"]["Authorization"] == "Bearer api-key"

    def test_no_custom_headers_keeps_default_headers(self, monkeypatch) -> None:
        auth = OAuth2Auth(
            token_url="https://example.com/token",
            client_id="cid",
            client_secret="csecret",
            token_auth="basic",
        )
        with patch("equinox.auth._oauth2.operations.httpx.Client", _FakeClient):
            execute_token_post(
                auth,
                {"grant_type": "client_credentials", "client_id": "cid", "client_secret": "cs"},
            )
        assert _FakeClient.last_call["headers"]["Authorization"].startswith("Basic ")


# --------------------------------------------------------------------------- #
# default_token_ttl_seconds
# --------------------------------------------------------------------------- #


class TestDefaultTokenTtl:
    def test_missing_expires_in_uses_configured_ttl(self, monkeypatch) -> None:
        auth = OAuth2Auth(
            token_url="https://integration.example.com/authenticate",
            client_id="cid",
            client_secret="csecret",
            token_field_name="JWTToken",
            default_token_ttl_seconds=86400,
        )
        monkeypatch.setattr(
            OAuth2Auth,
            "_post_token_request",
            lambda self, grant_data, **_: _ok_response({"JWTToken": "jwt-value"}),
        )

        before = utc_now()
        auth.apply_with_context(_request(), {})
        assert auth.expires_at is not None
        delta = (auth.expires_at - before).total_seconds()
        # expires_at = utc_now_at_apply + ttl; utc_now_at_apply >= before.
        assert 86400 <= delta <= 86410

    def test_expires_in_wins_over_configured_ttl(self, monkeypatch) -> None:
        auth = OAuth2Auth(
            token_url="https://example.com/token",
            client_id="cid",
            client_secret="csecret",
            default_token_ttl_seconds=86400,
        )
        monkeypatch.setattr(
            OAuth2Auth,
            "_post_token_request",
            lambda self, grant_data, **_: _ok_response(
                {"access_token": "t", "expires_in": 60},
            ),
        )
        before = utc_now()
        auth.apply_with_context(_request(), {})
        delta = (auth.expires_at - before).total_seconds()
        assert 60 <= delta <= 70


# --------------------------------------------------------------------------- #
# Validation fallbacks
# --------------------------------------------------------------------------- #


class TestValidationFallbacks:
    def test_invalid_field_name_falls_back_to_access_token(self) -> None:
        auth = OAuth2Auth(
            token_url="https://example.com/token",
            client_id="c",
            client_secret="s",
            token_field_name="   ",
        )
        assert auth.token_field_name == "access_token"

    def test_invalid_body_format_falls_back_to_form(self) -> None:
        auth = OAuth2Auth(
            token_url="https://example.com/token",
            client_id="c",
            client_secret="s",
            token_body_format="xml",  # type: ignore[arg-type]
        )
        assert auth.token_body_format == "form"

    def test_invalid_headers_type_is_ignored(self) -> None:
        auth = OAuth2Auth(
            token_url="https://example.com/token",
            client_id="c",
            client_secret="s",
            token_request_headers="not-a-dict",  # type: ignore[arg-type]
        )
        assert auth.token_request_headers is None

    def test_non_string_header_values_are_skipped(self) -> None:
        auth = OAuth2Auth(
            token_url="https://example.com/token",
            client_id="c",
            client_secret="s",
            token_request_headers={"X-Ok": "yes", "X-Bad": 42},  # type: ignore[dict-item]
        )
        assert auth.token_request_headers == {"X-Ok": "yes"}

    def test_invalid_ttl_falls_back_to_3600(self) -> None:
        auth = OAuth2Auth(
            token_url="https://example.com/token",
            client_id="c",
            client_secret="s",
            default_token_ttl_seconds=-5,
        )
        assert auth.default_token_ttl_seconds == 3600


# --------------------------------------------------------------------------- #
# Serialisation round-trip
# --------------------------------------------------------------------------- #


class TestRoundTrip:
    def test_to_dict_and_from_dict_preserve_new_fields(self) -> None:
        auth = OAuth2Auth(
            token_url="https://example.com/token",
            client_id="cid",
            client_secret="csecret",
            token_field_name="JWTToken",
            token_body_format="json",
            token_request_headers={"Authorization": "Bearer {{api_key}}"},
            default_token_ttl_seconds=86400,
        )
        data = auth.to_dict()
        assert data["token_field_name"] == "JWTToken"
        assert data["token_body_format"] == "json"
        assert data["token_request_headers"] == {"Authorization": "Bearer {{api_key}}"}
        assert data["default_token_ttl_seconds"] == 86400

        restored = OAuth2Auth.from_dict(data)
        assert restored.token_field_name == "JWTToken"
        assert restored.token_body_format == "json"
        assert restored.token_request_headers == {"Authorization": "Bearer {{api_key}}"}
        assert restored.default_token_ttl_seconds == 86400

    def test_from_dict_defaults_for_missing_fields(self) -> None:
        restored = OAuth2Auth.from_dict(
            {
                "type": "oauth2",
                "token_url": "https://example.com/token",
                "client_id": "cid",
                "client_secret": "csecret",
            },
        )
        assert restored.token_field_name == "access_token"
        assert restored.token_body_format == "form"
        assert restored.token_request_headers is None
        assert restored.default_token_ttl_seconds == 3600


# --------------------------------------------------------------------------- #
# Interpolation
# --------------------------------------------------------------------------- #


class TestInterpolate:
    def test_header_placeholders_are_interpolated(self) -> None:
        auth = OAuth2Auth(
            token_url="https://example.com/token",
            client_id="cid",
            client_secret="csecret",
            token_request_headers={"Authorization": "Bearer {{api_key}}"},
        )
        interpolated = auth.interpolate(lambda value: value.replace("{{api_key}}", "key-123"))
        assert interpolated is not None
        assert interpolated.token_request_headers == {"Authorization": "Bearer key-123"}
        # Original is untouched
        assert auth.token_request_headers == {"Authorization": "Bearer {{api_key}}"}
