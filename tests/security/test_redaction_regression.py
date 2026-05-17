"""Regression suite for security redaction helpers.

These tests protect against accidental sensitive-data leaks in logs/history.
"""

from __future__ import annotations

import pytest

from equinox.security import redact_body, redact_headers, redact_url, sanitize_details


@pytest.mark.parametrize(
    "headers,expected_key,expected_value",
    [
        ({"Authorization": "Bearer topsecret"}, "Authorization", "[REDACTED]"),
        ({"x-api-key": "abc123"}, "x-api-key", "[REDACTED]"),
        ({"Content-Type": "application/json"}, "Content-Type", "application/json"),
    ],
)
def test_redact_headers_regression(headers, expected_key, expected_value) -> None:
    redacted = redact_headers(headers)
    assert redacted[expected_key] == expected_value


@pytest.mark.parametrize(
    "body,secret_fragment",
    [
        ("password=hunter2&user=alice", "hunter2"),
        ('{"token":"abcdef","ok":true}', "abcdef"),
        ('{"client_secret":"super-secret"}', "super-secret"),
    ],
)
def test_redact_body_never_leaks_known_secrets(body: str, secret_fragment: str) -> None:
    out = redact_body(body)
    assert out is not None
    assert secret_fragment not in out
    assert "[REDACTED]" in out


def test_redact_url_masks_credentials_and_secret_params() -> None:
    url = "https://alice:pw123@example.com/api?token=abc123&x=1"
    out = redact_url(url)
    assert out is not None
    assert "pw123" not in out
    assert "abc123" not in out
    assert "***:***" in out
    assert "[REDACTED]" in out


def test_sanitize_details_nested_payloads() -> None:
    payload = {
        "token": "topsecret",
        "nested": {"authorization": "Bearer abc", "safe": "ok"},
        "items": [
            {"password": "pw"},
            "visible",
        ],
    }
    out = sanitize_details(payload)
    assert out["token"] == "[REDACTED]"
    assert out["nested"]["authorization"] == "[REDACTED]"
    assert out["nested"]["safe"] == "ok"
    assert out["items"][0]["password"] == "[REDACTED]"
