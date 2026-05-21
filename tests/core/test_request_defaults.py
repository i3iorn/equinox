"""100% coverage tests for equinox.core.request.defaults."""

from __future__ import annotations

from equinox.core.request import Request
from equinox.core.request.defaults import _SYSTEM_DEFAULTS, apply_default_headers


def test_apply_default_headers_adds_missing_headers() -> None:
    request = Request(method="GET", url="https://example.com")
    apply_default_headers(request)

    assert "X-Request-ID" in request.headers
    assert "User-Agent" in request.headers
    assert "Accept-Language" in request.headers
    assert "Accept-Encoding" in request.headers


def test_apply_default_headers_does_not_overwrite_existing() -> None:
    request = Request(
        method="GET",
        url="https://example.com",
        headers={
            "User-Agent": "MyAgent/1.0",
            "Accept-Language": "fr-FR",
        },
    )
    apply_default_headers(request)

    assert request.headers["User-Agent"] == "MyAgent/1.0"
    assert request.headers["Accept-Language"] == "fr-FR"
    # These were not set, so defaults should be applied
    assert "X-Request-ID" in request.headers
    assert "Accept-Encoding" in request.headers


def test_apply_default_headers_case_insensitive_existing_check() -> None:
    """Existing headers are checked case-insensitively — lowercase key is preserved."""
    request = Request(
        method="GET",
        url="https://example.com",
        headers={"user-agent": "TestClient"},
    )
    apply_default_headers(request)

    # The original value must not be overwritten by the default
    assert request.headers["user-agent"] == "TestClient"
    # The HeaderDict is case-insensitive, so both "user-agent" and "User-Agent" access
    # the same slot.  The default must not have overwritten the existing value.
    assert request.headers.get("User-Agent") == "TestClient"


def test_apply_default_headers_x_request_id_is_unique_per_call() -> None:
    r1 = Request(method="GET", url="https://example.com")
    r2 = Request(method="GET", url="https://example.com")
    apply_default_headers(r1)
    apply_default_headers(r2)

    assert r1.headers["X-Request-ID"] != r2.headers["X-Request-ID"]


def test_apply_default_headers_callable_value_is_invoked() -> None:
    """X-Request-ID uses a factory callable; verify it produces a non-empty string."""
    request = Request(method="GET", url="https://example.com")
    apply_default_headers(request)

    rid = request.headers.get("X-Request-ID", "")
    assert isinstance(rid, str) and len(rid) > 0


def test_system_defaults_contains_expected_keys() -> None:
    assert "X-Request-ID" in _SYSTEM_DEFAULTS
    assert "User-Agent" in _SYSTEM_DEFAULTS
    assert "Accept-Language" in _SYSTEM_DEFAULTS
    assert "Accept-Encoding" in _SYSTEM_DEFAULTS


def test_apply_default_headers_user_agent_contains_version() -> None:
    request = Request(method="GET", url="https://example.com")
    apply_default_headers(request)

    assert "Equinox" in request.headers["User-Agent"]

