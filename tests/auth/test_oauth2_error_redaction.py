from unittest.mock import MagicMock, patch

import httpx
import pytest

from equinox.auth import OAuth2Auth
from equinox.core.exceptions import AuthError


@patch("equinox.auth._oauth2.httpx.Client")
def test_http_status_error_message_redacts_sensitive_values(mock_client_class, monkeypatch):
    mock_client = MagicMock()
    mock_client_class.return_value.__enter__.return_value = mock_client

    request = httpx.Request("POST", "https://auth.example.com/token?client_secret=topsecret")
    response = httpx.Response(
        status_code=400,
        request=request,
        headers={"content-type": "application/json"},
        json={
            "error": "invalid_client",
            "client_secret": "supersecret",
            "access_token": "verylongtokenvalue123456789",
        },
    )
    mock_client.post.side_effect = httpx.HTTPStatusError(
        "Bad Request", request=request, response=response
    )

    auth = OAuth2Auth(
        client_id="c",
        client_secret="s",
        token_url="https://auth.example.com/token",
    )

    monkeypatch.setenv("EQUINOX_SSRF_ALLOW_ON_DNS_FAILURE", "1")

    with pytest.raises(AuthError) as exc_info:
        auth._refresh_access_token()

    assert mock_client.post.call_count == 1

    err_text = str(exc_info.value)
    assert "supersecret" not in err_text
    assert "verylongtokenvalue123456789" not in err_text

    details = exc_info.value.details
    assert details is not None
    token_response = details.get("token_response")
    assert token_response is not None
    assert token_response["body"]["client_secret"] == "[REDACTED]"
    assert token_response["body"]["access_token"] != "verylongtokenvalue123456789"

