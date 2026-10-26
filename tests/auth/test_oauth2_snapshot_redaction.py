"""Redaction of the OAuth2 token-response diagnostic snapshot.

Pins the raw-text fallback masking from issue #69: when the token endpoint
returns a non-JSON body, the diagnostic snapshot keeps a raw-text preview —
JWT-shaped substrings inside it must be masked regardless of field name, so
tokens leaked into free-form error messages never reach the GUI.
"""

from __future__ import annotations

import httpx
import pytest

from equinox.auth._oauth2.oauth2_auth import OAuth2Auth

# Syntactically valid JWT shape (header/payload/signature are not real tokens).
_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJlLWhlcmU"  # pragma: allowlist secret


def _response(body: str, status: int = 200) -> httpx.Response:
    request = httpx.Request("POST", "https://auth.example.com/token")
    return httpx.Response(
        status,
        text=body,
        headers={"content-type": "text/plain"},
        request=request,
    )


@pytest.fixture
def auth() -> OAuth2Auth:
    return OAuth2Auth(
        client_id="c",
        client_secret="s",
        token_url="https://auth.example.com/token",
    )


def test_non_json_body_masks_jwt_shaped_substring(auth: OAuth2Auth) -> None:
    response = _response(f"Authentication failed for session {_JWT} (user 42)")

    auth._capture_token_response(response)

    body = auth._last_token_response["body"]
    assert "_raw" in body
    assert _JWT not in body["_raw"]
    assert "eyJ[REDACTED-JWT]" in body["_raw"]
    # Non-secret context survives the masking.
    assert "Authentication failed" in body["_raw"]


def test_json_body_masks_known_fields_and_jwt(auth: OAuth2Auth) -> None:
    response = httpx.Response(
        200,
        json={"access_token": _JWT, "token_type": "Bearer"},
        request=httpx.Request("POST", "https://auth.example.com/token"),
    )

    auth._capture_token_response(response)

    body = auth._last_token_response["body"]
    assert _JWT not in str(body)
