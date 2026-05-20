"""Property-based regression tests for validation and parser hardening."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from equinox.core.exceptions import SecurityError, ValidationError
from equinox.core.io import parse_curl, parse_dotenv
from equinox.core.io.dotenv import MAX_DOTENV_SIZE
from equinox.core.validation import VALID_HTTP_METHODS, Validator


@given(st.text(min_size=0, max_size=32))
@settings(max_examples=200, deadline=None)
def test_validate_method_property(method_text: str) -> None:
    expected = method_text.upper().strip()
    if expected and expected in VALID_HTTP_METHODS:
        assert Validator.validate_method(method_text) == expected
    else:
        with pytest.raises(ValidationError):
            Validator.validate_method(method_text)


@given(st.text(min_size=0, max_size=256))
@settings(max_examples=250, deadline=None)
def test_validate_resolved_url_fuzz_does_not_raise_unexpected(url_text: str) -> None:
    try:
        result = Validator.validate_resolved_url(url_text)
        assert isinstance(result, str)
    except (ValidationError, SecurityError):
        # Expected hard failures for malformed/unsafe URLs.
        return


@given(st.text(min_size=0, max_size=300))
@settings(max_examples=250, deadline=None)
def test_parse_curl_fuzz_is_exception_bounded(command_text: str) -> None:
    try:
        parsed = parse_curl(command_text)
    except ValueError:
        return

    assert isinstance(parsed, dict)
    assert set(parsed.keys()) >= {"method", "url", "headers", "body", "verify_ssl"}
    assert isinstance(parsed["headers"], dict)


@given(st.text(min_size=0, max_size=512))
@settings(max_examples=200, deadline=None)
def test_parse_dotenv_fuzz_returns_dictionary_or_raises_size_limit(text: str) -> None:
    expected_size_error = f".env content exceeds maximum size ({MAX_DOTENV_SIZE} bytes)"
    try:
        parsed = parse_dotenv(text)
    except ValueError as exc:
        # Guard against false positives: only the documented size-limit error
        # is acceptable, and only when the generated payload exceeds the limit.
        assert len(text.encode("utf-8")) > MAX_DOTENV_SIZE
        assert str(exc) == expected_size_error
        return

    assert isinstance(parsed, dict)
    assert all(isinstance(k, str) for k in parsed.keys())
    assert all(isinstance(v, str) for v in parsed.values())
