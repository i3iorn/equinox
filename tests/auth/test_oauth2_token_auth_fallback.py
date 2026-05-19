from __future__ import annotations

import httpx
import pytest

from equinox.auth._oauth2 import OAuth2Auth
from equinox.core.exceptions import AuthError


def _http_status_error(status_code: int, payload: dict) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://plus.dnb.com/v3/token")
    response = httpx.Response(status_code, request=request, json=payload)
    return httpx.HTTPStatusError("token request failed", request=request, response=response)


def test_post_token_request_retries_with_basic_on_invalid_client(monkeypatch) -> None:
    auth = OAuth2Auth(
        token_url="https://plus.dnb.com/v3/token",
        client_id="client-id",
        client_secret="client-secret",
        token_auth="body",
    )

    calls = []
    ok_response = httpx.Response(
        200,
        request=httpx.Request("POST", "https://plus.dnb.com/v3/token"),
        json={"access_token": "token", "expires_in": 3600},
    )

    def fake_execute(self, grant_data, **_kwargs):
        calls.append(self.token_auth)
        if len(calls) == 1:
            raise _http_status_error(401, {"error": "invalid_client"})
        return ok_response

    monkeypatch.setattr(OAuth2Auth, "_execute_token_post", fake_execute)

    response = auth._post_token_request({"grant_type": "client_credentials", "client_id": "client-id", "client_secret": "client-secret"})

    assert response.status_code == 200
    assert calls == ["body", "basic"]
    assert auth.token_auth == "basic"


def test_post_token_request_keeps_mode_when_error_is_not_invalid_client(monkeypatch) -> None:
    auth = OAuth2Auth(
        token_url="https://plus.dnb.com/v3/token",
        client_id="client-id",
        client_secret="client-secret",
        token_auth="body",
    )

    calls = []

    def fake_execute(self, grant_data, **_kwargs):
        calls.append(self.token_auth)
        raise _http_status_error(401, {"error": "invalid_scope"})

    monkeypatch.setattr(OAuth2Auth, "_execute_token_post", fake_execute)

    with pytest.raises(AuthError, match="HTTP 401"):
        auth._post_token_request(
            {
                "grant_type": "client_credentials",
                "client_id": "client-id",
                "client_secret": "client-secret",
            }
        )

    assert calls == ["body"]
    assert auth.token_auth == "body"


def test_post_token_request_maps_failed_auth_mode_fallback_to_auth_error(monkeypatch) -> None:
    auth = OAuth2Auth(
        token_url="https://plus.dnb.com/v3/token",
        client_id="client-id",
        client_secret="client-secret",
        token_auth="body",
    )

    calls = []

    def fake_execute(self, grant_data, **_kwargs):
        calls.append(self.token_auth)
        if len(calls) == 1:
            raise _http_status_error(401, {"error": "invalid_client"})
        raise _http_status_error(400, {"error": "invalid_grant"})

    monkeypatch.setattr(OAuth2Auth, "_execute_token_post", fake_execute)

    with pytest.raises(AuthError, match="HTTP 401"):
        auth._post_token_request(
            {
                "grant_type": "client_credentials",
                "client_id": "client-id",
                "client_secret": "client-secret",
            }
        )

    assert calls == ["body", "basic"]
    assert auth.token_auth == "body"


def test_post_token_request_retries_client_credentials_when_refresh_grant_invalid(monkeypatch) -> None:
    auth = OAuth2Auth(
        token_url="https://plus.dnb.com/v3/token",
        client_id="client-id",
        client_secret="client-secret",
        refresh_token="stale-refresh-token",
        token_auth="basic",
    )

    calls = []
    ok_response = httpx.Response(
        200,
        request=httpx.Request("POST", "https://plus.dnb.com/v3/token"),
        json={"access_token": "token", "expires_in": 3600},
    )

    def fake_execute(self, grant_data, **_kwargs):
        calls.append((self.token_auth, grant_data.get("grant_type")))
        if grant_data.get("grant_type") == "refresh_token":
            raise _http_status_error(400, {"error": "invalid_grant"})
        return ok_response

    monkeypatch.setattr(OAuth2Auth, "_execute_token_post", fake_execute)

    response = auth._post_token_request(
        {
            "grant_type": "refresh_token",
            "refresh_token": "stale-refresh-token",
            "scope": "read",
        }
    )

    assert response.status_code == 200
    assert calls == [
        ("basic", "refresh_token"),
        ("basic", "client_credentials"),
    ]

