"""Tests for ``equinox.storage.utils`` with full branch coverage."""

from __future__ import annotations

import pytest

from equinox.core.exceptions import SecurityError, ValidationError
from equinox.storage import utils


def test_require_positive_int_accepts_positive_int() -> None:
    utils.require_positive_int(1, "ID")


@pytest.mark.parametrize("value", [0, -1, "1", None, 1.5])
def test_require_positive_int_rejects_invalid_values(value) -> None:
    with pytest.raises(ValidationError, match="positive integer"):
        utils.require_positive_int(value, "ID")


def test_validate_variable_key_strips_and_returns_key() -> None:
    assert utils.validate_variable_key("  API_KEY  ") == "API_KEY"


@pytest.mark.parametrize("key", ["", None, 123])
def test_validate_variable_key_rejects_non_string_or_empty(key) -> None:
    with pytest.raises(ValidationError, match="non-empty string"):
        utils.validate_variable_key(key)


def test_validate_variable_key_rejects_whitespace_only() -> None:
    with pytest.raises(ValidationError, match="empty or whitespace"):
        utils.validate_variable_key("   ")


def test_validate_variable_key_rejects_too_long() -> None:
    with pytest.raises(ValidationError, match="too long"):
        utils.validate_variable_key("a" * 5, max_length=4)


def test_validate_variable_value_returns_original_string() -> None:
    # Values should not be stripped; leading/trailing spaces can be intentional.
    assert utils.validate_variable_value("  secret  ") == "  secret  "


@pytest.mark.parametrize("value", [None, 1, b"x", ["x"]])
def test_validate_variable_value_rejects_non_string(value) -> None:
    with pytest.raises(ValidationError, match="must be a string"):
        utils.validate_variable_value(value)


def test_validate_variable_value_rejects_too_long() -> None:
    with pytest.raises(ValidationError, match="too long"):
        utils.validate_variable_value("abc", max_length=2)


def test_require_str_happy_path_and_strip() -> None:
    assert utils.require_str("  Name  ", "Field", 10) == "Name"


def test_require_str_none_when_required_rejected() -> None:
    with pytest.raises(ValidationError, match="non-empty string"):
        utils.require_str(None, "Field", 10, required=True)


def test_require_str_non_string_rejected() -> None:
    with pytest.raises(ValidationError, match="must be a string"):
        utils.require_str(123, "Field", 10)


def test_require_str_whitespace_only_when_required_rejected() -> None:
    with pytest.raises(ValidationError, match="empty or whitespace"):
        utils.require_str("   ", "Field", 10, required=True)


def test_require_str_too_long_rejected() -> None:
    with pytest.raises(ValidationError, match="too long"):
        utils.require_str("abcdef", "Field", 5)


def test_require_str_optional_allows_empty_and_strips() -> None:
    assert utils.require_str(None, "Field", 10, required=False) == ""
    assert utils.require_str("   ", "Field", 10, required=False) == ""


def test_coerce_body_to_str_none_returns_none() -> None:
    assert utils.coerce_body_to_str(None) is None


def test_coerce_body_to_str_decodes_bytes_leniently_and_strictly() -> None:
    assert utils.coerce_body_to_str(b"ok", strict=False) == "ok"
    assert utils.coerce_body_to_str(b"ok", strict=True) == "ok"


def test_coerce_body_to_str_invalid_utf8_lenient_vs_strict() -> None:
    bad = b"\xff"
    # Lenient mode uses replacement characters.
    assert utils.coerce_body_to_str(bad, strict=False) == "\ufffd"
    with pytest.raises(UnicodeDecodeError):
        utils.coerce_body_to_str(bad, strict=True)


def test_coerce_body_to_str_returns_plain_str_unchanged() -> None:
    assert utils.coerce_body_to_str("hello") == "hello"


def test_coerce_body_to_str_uses_str_for_other_objects() -> None:
    class ValueObj:
        def __str__(self) -> str:
            return "value"

    assert utils.coerce_body_to_str(ValueObj()) == "value"


def test_coerce_body_to_str_handles_str_conversion_failure() -> None:
    class BadStr:
        def __str__(self) -> str:
            raise RuntimeError("nope")

    assert utils.coerce_body_to_str(BadStr()) == ""


def test_safe_json_dumps_success_and_options() -> None:
    out = utils.safe_json_dumps({"b": 1, "a": "é"}, sort_keys=True, ensure_ascii=True)
    # ensure_ascii=True escapes non-ASCII and sort_keys orders fields.
    assert out == '{"a": "\\u00e9", "b": 1}'


def test_safe_json_dumps_enforces_max_len() -> None:
    with pytest.raises(SecurityError, match="exceeds 5 bytes"):
        utils.safe_json_dumps({"x": "123456"}, max_len=5)


def test_safe_json_loads_defaults_when_missing_input() -> None:
    # Omitted default uses {} sentinel resolution.
    assert utils.safe_json_loads(None) == {}
    assert utils.safe_json_loads("") == {}

    # Explicit default should be respected, including None.
    assert utils.safe_json_loads(None, default=[]) == []
    assert utils.safe_json_loads("", default=None) is None


def test_safe_json_loads_success() -> None:
    assert utils.safe_json_loads('{"a": 1}') == {"a": 1}


def test_safe_json_loads_parse_error_logs_debug_without_row_id(caplog) -> None:
    with caplog.at_level("DEBUG"):
        result = utils.safe_json_loads("{not json}", default={"fallback": True})
    assert result == {"fallback": True}
    assert "Failed to parse JSON" in caplog.text


def test_safe_json_loads_parse_error_logs_error_with_row_id(caplog) -> None:
    with caplog.at_level("ERROR"):
        result = utils.safe_json_loads("{not json}", default=[], row_id=42)
    assert result == []
    assert "Failed to parse JSON for row 42" in caplog.text
