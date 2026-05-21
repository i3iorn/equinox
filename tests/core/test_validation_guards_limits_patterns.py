"""Tests for core/validation _guards.py, _limits.py, _patterns.py (0% coverage)."""

from __future__ import annotations

import pytest

from equinox.core.exceptions import ValidationError
from equinox.core.validation._guards import _Guards
from equinox.core.validation._limits import VALID_HTTP_METHODS, _Limits
from equinox.core.validation._patterns import _Patterns

# ── _Guards ──────────────────────────────────────────────────────────────────


class TestGuardsRequireNonemptyStr:
    def test_valid_string_passes(self) -> None:
        _Guards.require_nonempty_str("hello", "field")  # no exception

    def test_empty_string_raises(self) -> None:
        with pytest.raises(ValidationError, match="non-empty string"):
            _Guards.require_nonempty_str("", "field")

    def test_none_raises(self) -> None:
        with pytest.raises(ValidationError, match="non-empty string"):
            _Guards.require_nonempty_str(None, "field")  # type: ignore[arg-type]

    def test_non_string_raises(self) -> None:
        with pytest.raises(ValidationError, match="non-empty string"):
            _Guards.require_nonempty_str(123, "field")  # type: ignore[arg-type]

    def test_field_name_in_message(self) -> None:
        with pytest.raises(ValidationError, match="my_field"):
            _Guards.require_nonempty_str("", "my_field")


class TestGuardsCheckCrlf:
    def test_no_crlf_passes(self) -> None:
        _Guards.check_crlf("normal value", "field")  # no exception

    def test_carriage_return_raises(self) -> None:
        with pytest.raises(ValidationError, match="CRLF"):
            _Guards.check_crlf("val\rue", "field")

    def test_newline_raises(self) -> None:
        with pytest.raises(ValidationError, match="CRLF"):
            _Guards.check_crlf("val\nue", "header")

    def test_crlf_in_value_raises(self) -> None:
        with pytest.raises(ValidationError):
            _Guards.check_crlf("x\r\ny", "h")


class TestGuardsCheckXssUrl:
    def test_safe_url_passes(self) -> None:
        _Guards.check_xss_url("https://example.com/path", "url")  # no exception

    def test_javascript_scheme_raises(self) -> None:
        with pytest.raises(ValidationError, match="malicious"):
            _Guards.check_xss_url("javascript:alert(1)", "url")

    def test_script_tag_raises(self) -> None:
        with pytest.raises(ValidationError):
            _Guards.check_xss_url("<script>alert(1)</script>", "url")

    def test_onload_handler_raises(self) -> None:
        with pytest.raises(ValidationError):
            _Guards.check_xss_url("?x=1&onload=evil()", "url")


# ── _Limits ───────────────────────────────────────────────────────────────────


class TestLimits:
    def test_http_methods_present(self) -> None:
        assert "GET" in VALID_HTTP_METHODS
        assert "POST" in VALID_HTTP_METHODS
        assert "DELETE" in VALID_HTTP_METHODS
        assert "PATCH" in VALID_HTTP_METHODS
        assert "PUT" in VALID_HTTP_METHODS
        assert "HEAD" in VALID_HTTP_METHODS
        assert "OPTIONS" in VALID_HTTP_METHODS

    def test_http_methods_is_frozenset(self) -> None:
        assert isinstance(VALID_HTTP_METHODS, frozenset)

    def test_limits_max_url_length(self) -> None:
        assert _Limits.MAX_URL_LENGTH == 2048

    def test_limits_max_header_values(self) -> None:
        assert _Limits.MAX_HEADER_NAME_LENGTH == 256
        assert _Limits.MAX_HEADER_LENGTH == 8192
        assert _Limits.MAX_HEADER_COUNT == 100

    def test_limits_max_body_size(self) -> None:
        assert _Limits.MAX_BODY_SIZE == 100 * 1024 * 1024

    def test_limits_max_param_values(self) -> None:
        assert _Limits.MAX_PARAM_COUNT == 100
        assert _Limits.MAX_PARAM_KEY_LENGTH == 256
        assert _Limits.MAX_PARAM_VALUE_LENGTH == 4096

    def test_limits_variable_lengths(self) -> None:
        assert _Limits.MAX_VARIABLE_NAME_LENGTH == 128
        assert _Limits.MAX_VARIABLE_VALUE_LENGTH == 4096


# ── _Patterns ─────────────────────────────────────────────────────────────────


class TestPatterns:
    def test_sql_injection_union_select(self) -> None:
        text = "UNION SELECT * FROM users"
        assert any(rx.search(text) for rx in _Patterns.SQL_INJECTION)

    def test_sql_injection_drop_table(self) -> None:
        assert any(rx.search("DROP TABLE users") for rx in _Patterns.SQL_INJECTION)

    def test_sql_injection_insert_into(self) -> None:
        assert any(rx.search("INSERT INTO users VALUES(1)") for rx in _Patterns.SQL_INJECTION)

    def test_sql_injection_delete_from(self) -> None:
        assert any(rx.search("DELETE FROM users") for rx in _Patterns.SQL_INJECTION)

    def test_sql_injection_update_set(self) -> None:
        assert any(rx.search("UPDATE users SET x=1") for rx in _Patterns.SQL_INJECTION)

    def test_sql_injection_comment(self) -> None:
        assert any(rx.search("val -- comment") for rx in _Patterns.SQL_INJECTION)

    def test_sql_injection_or_equals(self) -> None:
        assert any(rx.search("OR 1=1") for rx in _Patterns.SQL_INJECTION)

    def test_sql_injection_semicolon_drop(self) -> None:
        assert any(rx.search("; DROP TABLE users") for rx in _Patterns.SQL_INJECTION)

    def test_command_injection_semicolon(self) -> None:
        assert any(rx.search("val;cmd") for rx in _Patterns.COMMAND_INJECTION)

    def test_command_injection_pipe(self) -> None:
        assert any(rx.search("val|cmd") for rx in _Patterns.COMMAND_INJECTION)

    def test_command_injection_backtick(self) -> None:
        assert any(rx.search("`cmd`") for rx in _Patterns.COMMAND_INJECTION)

    def test_command_injection_dollar_paren(self) -> None:
        assert any(rx.search("$(cmd)") for rx in _Patterns.COMMAND_INJECTION)

    def test_command_injection_dollar_brace(self) -> None:
        assert any(rx.search("${VAR}") for rx in _Patterns.COMMAND_INJECTION)

    def test_xss_full_script_tag(self) -> None:
        assert any(rx.search("<script>evil</script>") for rx in _Patterns.XSS_FULL)

    def test_xss_full_javascript(self) -> None:
        assert any(rx.search("javascript:evil()") for rx in _Patterns.XSS_FULL)

    def test_xss_full_on_handler(self) -> None:
        assert any(rx.search("onload=evil") for rx in _Patterns.XSS_FULL)

    def test_xss_full_iframe(self) -> None:
        assert any(rx.search("<iframe src=evil>") for rx in _Patterns.XSS_FULL)

    def test_xss_url_is_subset_of_xss_full(self) -> None:
        assert len(_Patterns.XSS_URL) <= len(_Patterns.XSS_FULL)

    def test_path_traversal_dot_dot_slash(self) -> None:
        assert any(rx.search("../../etc/passwd") for rx in _Patterns.PATH_TRAVERSAL)

    def test_path_traversal_dot_dot_backslash(self) -> None:
        assert any(rx.search("..\\..\\windows") for rx in _Patterns.PATH_TRAVERSAL)

    def test_path_traversal_trailing(self) -> None:
        assert any(rx.search("path/..") for rx in _Patterns.PATH_TRAVERSAL)

    def test_path_traversal_home_tilde(self) -> None:
        assert any(rx.search("~/etc/passwd") for rx in _Patterns.PATH_TRAVERSAL)

    def test_header_name_pattern_valid(self) -> None:
        assert _Patterns.HEADER_NAME.match("Content-Type")
        assert _Patterns.HEADER_NAME.match("X-Custom-Header")

    def test_header_name_pattern_invalid(self) -> None:
        assert not _Patterns.HEADER_NAME.match("My Header")
        assert not _Patterns.HEADER_NAME.match("")

    def test_variable_name_pattern_valid(self) -> None:
        assert _Patterns.VARIABLE_NAME.match("MY_VAR")
        assert _Patterns.VARIABLE_NAME.match("_private")
        assert _Patterns.VARIABLE_NAME.match("var123")

    def test_variable_name_pattern_invalid(self) -> None:
        assert not _Patterns.VARIABLE_NAME.match("123var")
        assert not _Patterns.VARIABLE_NAME.match("")

    def test_trailing_comma_json_pattern(self) -> None:
        match = _Patterns.TRAILING_COMMA_JSON.search('{"a": 1,}')
        assert match is not None
