"""Tests for equinox.core.redact — centralised credential redaction."""

import pytest

from equinox.core.redact import (
    redact_headers,
    redact_body,
    redact_url,
    SENSITIVE_HEADER_NAMES,
)


# ── redact_headers ────────────────────────────────────────────────────────────

class TestRedactHeaders:
    def test_sensitive_keys_are_redacted(self):
        headers = {
            "Authorization": "Bearer super-secret-token",
            "Content-Type": "application/json",
            "X-Api-Key": "my-key-123",
            "Cookie": "session=abc",
            "Accept": "*/*",
        }
        safe = redact_headers(headers)
        assert safe["Authorization"] == "[REDACTED]"
        assert safe["X-Api-Key"] == "[REDACTED]"
        assert safe["Cookie"] == "[REDACTED]"
        # Non-sensitive headers untouched
        assert safe["Content-Type"] == "application/json"
        assert safe["Accept"] == "*/*"

    def test_case_insensitive(self):
        headers = {"authorization": "Bearer tok", "COOKIE": "sess=1"}
        safe = redact_headers(headers)
        assert safe["authorization"] == "[REDACTED]"
        assert safe["COOKIE"] == "[REDACTED]"

    def test_empty_and_none(self):
        assert redact_headers(None) == {}
        assert redact_headers({}) == {}

    def test_set_cookie_redacted(self):
        headers = {"Set-Cookie": "sid=xyz; path=/; HttpOnly"}
        safe = redact_headers(headers)
        assert safe["Set-Cookie"] == "[REDACTED]"

    def test_proxy_authorization(self):
        headers = {"Proxy-Authorization": "Basic dXNlcjpwYXNz"}
        safe = redact_headers(headers)
        assert safe["Proxy-Authorization"] == "[REDACTED]"

    def test_all_sensitive_names_covered(self):
        """Every name in the canonical set must be redacted."""
        for name in SENSITIVE_HEADER_NAMES:
            # Build a header dict with title-cased key
            titled = "-".join(p.capitalize() for p in name.split("-"))
            headers = {titled: "secret-value"}
            safe = redact_headers(headers)
            assert safe[titled] == "[REDACTED]", f"{titled} was not redacted"


# ── redact_body ───────────────────────────────────────────────────────────────

class TestRedactBody:
    def test_form_encoded_secrets(self):
        body = "grant_type=client_credentials&client_secret=SUPER_SECRET&client_id=my-app"
        safe = redact_body(body)
        assert "SUPER_SECRET" not in safe
        assert "client_secret=[REDACTED]" in safe
        assert "client_id=my-app" in safe  # not a secret key

    def test_json_secrets(self):
        body = '{"client_secret": "abc123", "username": "alice"}'
        safe = redact_body(body)
        assert "abc123" not in safe
        assert '"client_secret": "[REDACTED]"' in safe
        assert '"username": "alice"' in safe

    def test_password_form_field(self):
        body = "username=admin&password=hunter2"
        safe = redact_body(body)
        assert "hunter2" not in safe
        assert "password=[REDACTED]" in safe

    def test_access_token_form(self):
        body = "access_token=eyJhbGc&scope=read"
        safe = redact_body(body)
        assert "eyJhbGc" not in safe
        assert "scope=read" in safe

    def test_refresh_token_json(self):
        body = '{"refresh_token": "rt_xyz", "expires_in": 3600}'
        safe = redact_body(body)
        assert "rt_xyz" not in safe
        assert "3600" in safe

    def test_none_and_empty(self):
        assert redact_body(None) is None
        assert redact_body("") == ""

    def test_no_secrets_unchanged(self):
        body = '{"name": "test", "value": 42}'
        assert redact_body(body) == body

    def test_max_length_truncation(self):
        body = "a" * 2000
        result = redact_body(body, max_length=100)
        assert len(result) < 200
        assert "TRUNCATED" in result


# ── redact_url ────────────────────────────────────────────────────────────────

class TestRedactUrl:
    def test_embedded_credentials(self):
        url = "https://admin:s3cret@api.example.com/v1/users"
        safe = redact_url(url)
        assert "s3cret" not in safe
        assert "admin" not in safe
        assert "***:***@" in safe
        assert "api.example.com/v1/users" in safe

    def test_secret_query_params(self):
        url = "https://api.example.com/data?api_key=ABCD1234&format=json"
        safe = redact_url(url)
        assert "ABCD1234" not in safe
        assert "api_key=[REDACTED]" in safe
        assert "format=json" in safe

    def test_token_query_param(self):
        url = "https://api.example.com/data?token=xyz123"
        safe = redact_url(url)
        assert "xyz123" not in safe

    def test_clean_url_unchanged(self):
        url = "https://api.example.com/v1/users?page=1&limit=20"
        assert redact_url(url) == url

    def test_empty_and_none(self):
        assert redact_url("") == ""
        assert redact_url(None) is None

