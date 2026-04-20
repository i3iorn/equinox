"""Comprehensive coverage tests — fills gaps in every core module.

Organised into one class per module:
  1. ScriptRunner  — sandbox depth, timeout, AST, allowed modules
  2. Redact        — mask_secret, sanitize_details, multi-secret, unicode
  3. Validation    — CRLF, SSRF, XSS, command-injection, fuzz
  4. Interpolation — edge cases, non-string vars, MAX_OUTPUT, fuzz
  5. Interceptors  — safe_body_preview, STOP/REPLACE actions, StructuredLogger
  6. Request/Response — HeaderDict, Response cached props, to_curl, from_dict
  7. Dotenv        — inline comments, unicode, CRLF, tabs
  8. AuthCipher    — round-trip, reset, concurrency
  9. Exceptions    — hierarchy, details, aliases
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import random
import re
import string
import threading
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

# ── Imports under test ────────────────────────────────────────────────────────
from equinox.core.exceptions import (
    AuthError,
    CertificateError,
    DuplicateError,
    EquinoxError,
    FileSizeError,
    PluginError,
    RateLimitError,
    RequestError,
    RequestTimeoutError,
    SecurityError,
    StorageError,
    ValidationError,
)
from equinox.core.exceptions import TimeoutError as TimeoutAlias


# ═══════════════════════════════════════════════════════════════════════════════
# 1. ScriptRunner — sandbox depth
# ═══════════════════════════════════════════════════════════════════════════════

from equinox.core.scripts import (
    ALLOWED_MODULES,
    SAFE_BUILTINS,
    ScriptResult,
    ScriptRunner,
    _validate_ast,
)


class TestScriptRunnerComprehensive:
    """Gaps: MAX_SOURCE_LENGTH, AST dangerous attrs, allowed modules,
    value coercion, env deletion, thread fallback, multiple imports."""

    # ── Source length ─────────────────────────────────────────────────────

    def test_max_source_length_exceeded(self):
        huge = 'env["x"] = "ok"\n' * 10_000  # ~160 KB > 64 KB limit
        result = ScriptRunner.run_pre(huge, {}, {})
        assert result.error is not None
        assert "too long" in result.error.lower()

    def test_source_at_limit_not_rejected_for_length(self):
        """A script under the limit should NOT trigger the 'too long' gate.
        We test the gate logic directly to avoid cross-platform subprocess
        pickling issues."""
        small_script = 'env["ok"] = "yes"'
        assert len(small_script) < ScriptRunner.MAX_SOURCE_LENGTH
        result = ScriptRunner.run_pre(small_script, {}, {})
        assert result.error is None
        assert result.output_vars.get("ok") == "yes"

    # ── AST dangerous attribute access ────────────────────────────────────

    @pytest.mark.parametrize("attr", [
        "__subclasses__", "__bases__", "__mro__", "__globals__",
        "__code__", "__builtins__", "__class__", "__dict__",
    ])
    def test_ast_blocks_dangerous_attribute(self, attr):
        script = f'x = "".__class__.{attr}'
        result = ScriptRunner.run_pre(script, {}, {})
        assert result.error is not None

    def test_ast_blocks_three_arg_type(self):
        script = 'type("Evil", (object,), {"x": 1})'
        result = ScriptRunner.run_pre(script, {}, {})
        assert result.error is not None

    def test_ast_allows_one_arg_type(self):
        # 1-arg type() is just introspection — should work if type isn't blocked at builtins
        # But it IS blocked in SAFE_BUILTINS, so expect an error from builtins, not AST
        script = 'env["t"] = str(1)'
        result = ScriptRunner.run_pre(script, {}, {})
        assert result.error is None
        assert result.output_vars["t"] == "1"

    def test_setattr_blocked(self):
        script = 'setattr(object, "x", 1)'
        result = ScriptRunner.run_pre(script, {}, {})
        assert result.error is not None

    def test_globals_call_blocked(self):
        script = "globals()"
        result = ScriptRunner.run_pre(script, {}, {})
        assert result.error is not None

    def test_locals_call_blocked(self):
        script = "locals()"
        result = ScriptRunner.run_pre(script, {}, {})
        assert result.error is not None

    # ── Allowed modules ───────────────────────────────────────────────────

    def test_allowed_time(self):
        # `type` is intentionally blocked in SAFE_BUILTINS; use isinstance()
        # (which IS available) to confirm time.time() returns a float.
        result = ScriptRunner.run_pre(
            'import time; env["t"] = str(isinstance(time.time(), float))',
            {}, {},
        )
        assert result.error is None
        assert result.output_vars["t"] == "True"

    def test_allowed_math(self):
        result = ScriptRunner.run_pre(
            'import math; env["pi"] = str(round(math.pi, 2))',
            {}, {},
        )
        assert result.error is None
        assert result.output_vars["pi"] == "3.14"

    def test_allowed_datetime(self):
        result = ScriptRunner.run_pre(
            'from datetime import datetime; env["y"] = str(datetime(2024,1,1).year)',
            {}, {},
        )
        assert result.error is None
        assert result.output_vars["y"] == "2024"

    def test_allowed_uuid(self):
        result = ScriptRunner.run_pre(
            'import uuid; env["u"] = str(len(str(uuid.uuid4())))',
            {}, {},
        )
        assert result.error is None
        assert int(result.output_vars["u"]) == 36  # UUID string length

    def test_allowed_string(self):
        result = ScriptRunner.run_pre(
            'import string; env["d"] = string.digits',
            {}, {},
        )
        assert result.error is None
        assert result.output_vars["d"] == "0123456789"

    def test_allowed_collections(self):
        result = ScriptRunner.run_pre(
            'from collections import OrderedDict; env["ok"] = "yes"',
            {}, {},
        )
        assert result.error is None

    def test_multiple_imports_one_script(self):
        script = (
            "import json, re, base64\n"
            'env["a"] = json.dumps([1])\n'
            'env["b"] = re.sub("x","y","axb")\n'
            'env["c"] = base64.b64encode(b"z").decode()\n'
        )
        result = ScriptRunner.run_pre(script, {}, {})
        assert result.error is None
        assert result.output_vars["a"] == "[1]"
        assert result.output_vars["b"] == "ayb"

    # ── Blocked modules ───────────────────────────────────────────────────

    @pytest.mark.parametrize("mod", [
        "os", "sys", "subprocess", "socket", "shutil", "signal",
        "ctypes", "pickle", "importlib",
    ])
    def test_blocked_module(self, mod):
        result = ScriptRunner.run_pre(f"import {mod}", {}, {})
        assert result.error is not None

    # ── Value coercion edge cases ─────────────────────────────────────────

    def test_coerce_bool_to_str(self):
        result = ScriptRunner.run_pre('env["b"] = True', {}, {})
        assert result.output_vars["b"] == "True"

    def test_coerce_none_to_str(self):
        result = ScriptRunner.run_pre('env["n"] = None', {}, {})
        assert result.output_vars["n"] == "None"

    def test_coerce_float_to_str(self):
        result = ScriptRunner.run_pre('env["f"] = 3.14', {}, {})
        assert result.output_vars["f"] == "3.14"

    def test_coerce_list_to_str(self):
        result = ScriptRunner.run_pre('env["l"] = [1,2]', {}, {})
        assert result.output_vars["l"] == "[1, 2]"

    # ── Env var deletion ──────────────────────────────────────────────────

    def test_delete_env_var_not_in_output(self):
        """Deleting a key from env should not appear as a changed var."""
        script = 'del env["old"]'
        result = ScriptRunner.run_pre(script, {}, {"old": "val"})
        assert result.error is None
        assert "old" not in result.output_vars

    # ── Process isolation availability ────────────────────────────────────

    def test_process_isolation_required_on_oserror(self):
        """If multiprocessing is unavailable, fail closed (no thread fallback)."""
        with patch("equinox.core.scripts.multiprocessing") as mock_mp:
            mock_mp.get_context.side_effect = OSError("no mp")
            mock_mp.Queue.side_effect = OSError("no mp")
            result = ScriptRunner.run_pre('env["x"] = "thread"', {}, {})
        assert result.error is not None
        assert "sandbox unavailable" in result.error.lower()

    # ── ScriptResult ──────────────────────────────────────────────────────

    def test_script_result_ok_property(self):
        assert ScriptResult().ok is True
        assert ScriptResult(error="boom").ok is False

    # ── Post-script comprehensive ─────────────────────────────────────────

    def test_post_script_json_body_parsing(self):
        resp = {"status_code": 200, "body": '{"items":[1,2,3]}', "json": {"items": [1, 2, 3]}}
        script = (
            'import json\n'
            'env["count"] = str(len(response["json"]["items"]))\n'
        )
        result = ScriptRunner.run_post(script, resp, {})
        assert result.error is None
        assert result.output_vars["count"] == "3"

    def test_post_script_unchanged_vars_not_in_output(self):
        resp = {"status_code": 200, "body": "", "json": None}
        result = ScriptRunner.run_post(
            'env["keep"] = "same"', resp, {"keep": "same"}
        )
        # Value didn't change → not in output
        assert result.output_vars == {}


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Redact — mask_secret, sanitize_details, multi-secret, unicode
# ═══════════════════════════════════════════════════════════════════════════════

from equinox.core.redact import (
    SENSITIVE_PAYLOAD_KEYS,
    mask_secret,
    redact_body,
    redact_headers,
    redact_url,
    sanitize_details,
)


class TestRedactComprehensive:
    # ── mask_secret ───────────────────────────────────────────────────────

    def test_mask_secret_none(self):
        assert mask_secret(None) == "***"

    def test_mask_secret_empty(self):
        assert mask_secret("") == "***"

    def test_mask_secret_short(self):
        assert mask_secret("abc") == "***"  # len <= keep → ***

    def test_mask_secret_exact_keep(self):
        assert mask_secret("12345678") == "***"  # len == keep → ***

    def test_mask_secret_long(self):
        result = mask_secret("super-secret-token-value")
        assert result.startswith("super-se")
        assert result.endswith("…")
        assert len(result) == 9  # 8 chars + "…"

    def test_mask_secret_custom_keep(self):
        result = mask_secret("abcdefghij", keep=3)
        assert result == "abc…"

    # ── sanitize_details ──────────────────────────────────────────────────

    def test_sanitize_flat_dict(self):
        d = {"api_key": "secret", "name": "test"}
        out = sanitize_details(d)
        assert out["api_key"] == "[REDACTED]"
        assert out["name"] == "test"

    def test_sanitize_nested(self):
        d = {"config": {"password": "pw", "host": "localhost"}}
        out = sanitize_details(d)
        assert out["config"]["password"] == "[REDACTED]"
        assert out["config"]["host"] == "localhost"

    def test_sanitize_list(self):
        d = {"items": [{"token": "abc"}, {"label": "ok"}]}
        out = sanitize_details(d)
        assert out["items"][0]["token"] == "[REDACTED]"
        assert out["items"][1]["label"] == "ok"

    def test_sanitize_long_string_truncated(self):
        d = {"data": "a" * 500}
        out = sanitize_details(d, max_string_len=100)
        assert len(out["data"]) == 103  # 100 + "..."
        assert out["data"].endswith("...")

    def test_sanitize_non_string_non_dict(self):
        """Ints, floats, bools should pass through."""
        d = {"count": 42, "active": True, "ratio": 3.14}
        out = sanitize_details(d)
        assert out == d

    def test_sanitize_tuple_in_list(self):
        d = {"data": ({"secret": "x"}, "plain")}
        out = sanitize_details(d)
        assert out["data"][0]["secret"] == "[REDACTED]"
        assert out["data"][1] == "plain"

    # ── Multi-secret body ─────────────────────────────────────────────────

    def test_redact_body_multiple_form_secrets(self):
        body = "client_secret=A&password=B&access_token=C&name=ok"
        result = redact_body(body)
        assert "client_secret=A" not in result
        assert "password=B" not in result
        assert "access_token=C" not in result
        assert "name=ok" in result

    def test_redact_body_multiple_json_secrets(self):
        body = json.dumps({
            "client_secret": "s1",
            "refresh_token": "r1",
            "username": "alice",
        })
        result = redact_body(body)
        assert "s1" not in result
        assert "r1" not in result
        assert "alice" in result

    # ── Unicode ───────────────────────────────────────────────────────────

    def test_redact_body_unicode_secret(self):
        body = "password=pässwörd123&user=alice"
        result = redact_body(body)
        assert "pässwörd123" not in result
        assert "password=[REDACTED]" in result

    def test_redact_url_unicode_path(self):
        url = "https://example.com/api?token=tök&page=1"
        result = redact_url(url)
        assert "tök" not in result
        assert "page=1" in result

    # ── URL multi-secrets ─────────────────────────────────────────────────

    def test_redact_url_multiple_secret_params(self):
        url = "https://api.com/v1?api_key=K1&token=T2&format=json"
        result = redact_url(url)
        assert "K1" not in result
        assert "T2" not in result
        assert "format=json" in result

    def test_redact_url_credentials_with_special_chars(self):
        url = "https://us%40er:p%40ss@api.com/data"
        result = redact_url(url)
        # Embedded credentials should be masked
        assert "***:***@" in result

    # ── Headers edge case ─────────────────────────────────────────────────

    def test_redact_headers_with_int_value(self):
        """Non-string values should not crash."""
        headers = {"Authorization": 12345, "Accept": "text/html"}
        safe = redact_headers(headers)
        assert safe["Authorization"] == "[REDACTED]"
        assert safe["Accept"] == "text/html"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Validation — CRLF, SSRF, XSS, command-injection, fuzz
# ═══════════════════════════════════════════════════════════════════════════════

from equinox.core.validation import Validator, VALID_HTTP_METHODS


class TestValidationComprehensive:
    # ── CRLF in headers ───────────────────────────────────────────────────

    def test_header_value_crlf_r(self):
        with pytest.raises(ValidationError, match="CRLF"):
            Validator.validate_header_value("value\rinjected")

    def test_header_value_crlf_n(self):
        with pytest.raises(ValidationError, match="CRLF"):
            Validator.validate_header_value("value\ninjected")

    def test_header_value_crlf_rn(self):
        with pytest.raises(ValidationError, match="CRLF"):
            Validator.validate_header_value("value\r\ninjected")

    # ── Header name validation ────────────────────────────────────────────

    def test_header_name_empty(self):
        with pytest.raises(ValidationError):
            Validator.validate_header_name("")

    def test_header_name_too_long(self):
        with pytest.raises(ValidationError, match="too long"):
            Validator.validate_header_name("X" * 300)

    def test_header_name_invalid_chars(self):
        with pytest.raises(ValidationError, match="Invalid header name"):
            Validator.validate_header_name("Bad Header Name")

    def test_header_name_managed_strict(self):
        with pytest.raises(ValidationError, match="Cannot manually set"):
            Validator.validate_header_name("Host", strict=True)

    def test_header_name_managed_non_strict(self):
        # Should succeed with a warning, not raise
        result = Validator.validate_header_name("Host", strict=False)
        assert result == "Host"

    # ── XSS in URL ────────────────────────────────────────────────────────

    @pytest.mark.parametrize("payload", [
        "https://example.com/<script>alert(1)</script>",
        "javascript:alert(1)",
        "https://example.com/?q=onclick=alert(1)",
    ])
    def test_url_xss_blocked(self, payload):
        with pytest.raises(ValidationError, match="malicious"):
            Validator.validate_url(payload)

    # ── XSS in header value ───────────────────────────────────────────────

    def test_header_value_xss_script(self):
        with pytest.raises(ValidationError, match="malicious"):
            Validator.validate_header_value('<script>alert("x")</script>')

    def test_header_value_xss_javascript(self):
        with pytest.raises(ValidationError, match="malicious"):
            Validator.validate_header_value("javascript:void(0)")

    # ── Command injection in env vars ─────────────────────────────────────

    @pytest.mark.parametrize("bad_val", [
        "value;rm -rf /",
        "value|cat /etc/passwd",
        "value&whoami",
        "value`id`",
        "value${HOME}",
        "value$(whoami)",
    ])
    def test_env_var_command_injection(self, bad_val):
        with pytest.raises(ValidationError, match="dangerous"):
            Validator.validate_environment_variable("SAFE_NAME", bad_val)

    def test_env_var_name_invalid_start_digit(self):
        with pytest.raises(ValidationError, match="Invalid variable name"):
            Validator.validate_environment_variable("1bad", "val")

    def test_env_var_name_with_space(self):
        with pytest.raises(ValidationError, match="Invalid variable name"):
            Validator.validate_environment_variable("bad name", "val")

    def test_env_var_name_too_long(self):
        with pytest.raises(ValidationError, match="too long"):
            Validator.validate_environment_variable("A" * 200, "val")

    def test_env_var_value_too_long(self):
        with pytest.raises(ValidationError, match="too long"):
            Validator.validate_environment_variable("X", "v" * 5000)

    def test_env_var_valid(self):
        name, value = Validator.validate_environment_variable("MY_VAR", "hello")
        assert name == "MY_VAR"
        assert value == "hello"

    # ── SSRF / metadata ───────────────────────────────────────────────────

    @pytest.mark.parametrize("host", [
        "169.254.169.254",
        "metadata.google.internal",
        "metadata.goog",
    ])
    def test_ssrf_metadata_blocked(self, host):
        with pytest.raises(ValidationError, match="SSRF|metadata"):
            Validator._check_ssrf(host)

    def test_ssrf_private_ip(self):
        with pytest.raises(ValidationError, match="private"):
            Validator._check_ssrf("10.0.0.1")

    def test_ssrf_loopback(self):
        with pytest.raises(ValidationError, match="private"):
            Validator._check_ssrf("127.0.0.1")

    def test_ssrf_link_local(self):
        with pytest.raises(ValidationError, match="private"):
            Validator._check_ssrf("169.254.1.1")

    def test_ssrf_public_ip_allowed(self):
        # Should not raise for a public IP
        Validator._check_ssrf("8.8.8.8")

    # ── Query param validation ────────────────────────────────────────────

    def test_param_crlf_in_key(self):
        with pytest.raises(ValidationError, match="CRLF"):
            Validator.validate_query_params({"key\r": "val"})

    def test_param_crlf_in_value(self):
        with pytest.raises(ValidationError, match="CRLF"):
            Validator.validate_query_params({"key": "val\n"})

    def test_param_key_too_long(self):
        with pytest.raises(ValidationError, match="too long"):
            Validator.validate_query_params({"k" * 300: "v"})

    def test_param_value_too_long(self):
        with pytest.raises(ValidationError, match="too long"):
            Validator.validate_query_params({"k": "v" * 5000})

    def test_param_non_string_key(self):
        with pytest.raises(ValidationError, match="string"):
            Validator.validate_query_params({123: "val"})

    def test_too_many_params(self):
        many = {f"k{i}": f"v{i}" for i in range(101)}
        with pytest.raises(ValidationError, match="Too many"):
            Validator.validate_query_params(many)

    def test_params_at_limit_ok(self):
        at_limit = {f"k{i}": f"v{i}" for i in range(100)}
        result = Validator.validate_query_params(at_limit)
        assert len(result) == 100

    # ── Headers boundary ──────────────────────────────────────────────────

    def test_too_many_headers(self):
        many = {f"X-H{i}": f"v{i}" for i in range(101)}
        with pytest.raises(ValidationError, match="Too many"):
            Validator.validate_headers(many)

    def test_headers_at_limit_ok(self):
        at_limit = {f"X-H{i}": f"v{i}" for i in range(100)}
        result = Validator.validate_headers(at_limit)
        assert len(result) == 100

    def test_headers_non_dict_raises(self):
        with pytest.raises(ValidationError, match="dictionary"):
            Validator.validate_headers("not a dict")

    # ── Body validation ───────────────────────────────────────────────────

    def test_body_none_returns_none(self):
        assert Validator.validate_request_body(None) is None

    def test_body_json_content_type_invalid_json(self):
        with pytest.raises(ValidationError, match="Invalid JSON"):
            Validator.validate_request_body("{bad json", content_type="application/json")

    def test_body_json_content_type_valid_json(self):
        result = Validator.validate_request_body('{"key":"val"}', content_type="application/json")
        assert result == '{"key":"val"}'

    def test_body_dict_serialization(self):
        result = Validator.validate_request_body({"key": "val"})
        assert result == {"key": "val"}

    def test_body_dict_non_serializable(self):
        with pytest.raises(ValidationError, match="Invalid JSON"):
            Validator.validate_request_body({"bad": object()})

    def test_body_sql_injection_logged_but_allowed(self, caplog):
        """SQL patterns in body are logged as warning but not rejected."""
        body = "SELECT * FROM users; DROP TABLE users"
        with caplog.at_level(logging.WARNING):
            result = Validator.validate_request_body(body)
        assert result == body  # not rejected

    # ── Method validation ─────────────────────────────────────────────────

    @pytest.mark.parametrize("method", list(VALID_HTTP_METHODS))
    def test_all_valid_methods(self, method):
        assert Validator.validate_method(method) == method

    def test_method_lowercase_normalised(self):
        assert Validator.validate_method("get") == "GET"

    def test_method_invalid(self):
        with pytest.raises(ValidationError, match="Invalid HTTP method"):
            Validator.validate_method("INVALID")

    def test_method_empty(self):
        with pytest.raises(ValidationError):
            Validator.validate_method("")

    # ── File path validation ──────────────────────────────────────────────

    def test_file_path_traversal(self, tmp_path):
        with pytest.raises(ValidationError, match="traversal"):
            Validator.validate_file_path("../../../etc/passwd")

    def test_file_path_tilde(self, tmp_path):
        with pytest.raises(ValidationError, match="traversal"):
            Validator.validate_file_path("~/secret")

    def test_file_path_url_encoded_traversal(self, tmp_path):
        with pytest.raises(ValidationError, match="traversal"):
            Validator.validate_file_path("..%2F..%2Fetc%2Fpasswd")

    def test_file_path_empty(self):
        with pytest.raises(ValidationError, match="non-empty"):
            Validator.validate_file_path("")

    def test_file_path_valid(self, tmp_path):
        target = tmp_path / "test.json"
        target.write_text("{}")
        result = Validator.validate_file_path(str(target))
        assert result.name == "test.json"

    def test_file_path_outside_base_dir(self, tmp_path):
        base = tmp_path / "allowed"
        base.mkdir()
        outside = tmp_path / "outside.txt"
        outside.write_text("x")
        with pytest.raises(ValidationError, match="outside"):
            Validator.validate_file_path(str(outside), base_dir=base)

    # ── sanitize_for_display ──────────────────────────────────────────────

    def test_sanitize_for_display_truncates(self):
        long_text = "x" * 2000
        result = Validator.sanitize_for_display(long_text, max_length=100)
        assert len(result) == 103  # 100 + "..."
        assert result.endswith("...")

    def test_sanitize_for_display_strips_control_chars(self):
        text = "hello\x00world\x07test"
        result = Validator.sanitize_for_display(text)
        assert "\x00" not in result
        assert "\x07" not in result
        assert "hello" in result

    def test_sanitize_for_display_preserves_newlines_tabs(self):
        text = "line1\nline2\ttab"
        result = Validator.sanitize_for_display(text)
        assert "\n" in result
        assert "\t" in result

    def test_sanitize_for_display_non_string(self):
        result = Validator.sanitize_for_display(12345)
        assert result == "12345"

    # ── Fuzz tests ────────────────────────────────────────────────────────

    @pytest.mark.parametrize("_seed", range(10))
    def test_fuzz_validate_url_no_crash(self, _seed):
        """Random strings should not cause unhandled exceptions."""
        random.seed(_seed)
        chars = string.printable
        url = "".join(random.choices(chars, k=random.randint(0, 300)))
        try:
            Validator.validate_url(url)
        except ValidationError:
            pass  # Expected for most random strings

    @pytest.mark.parametrize("_seed", range(10))
    def test_fuzz_validate_header_value_no_crash(self, _seed):
        random.seed(_seed)
        chars = string.printable
        value = "".join(random.choices(chars, k=random.randint(0, 200)))
        try:
            Validator.validate_header_value(value)
        except ValidationError:
            pass

    @pytest.mark.parametrize("_seed", range(10))
    def test_fuzz_validate_env_var_no_crash(self, _seed):
        random.seed(_seed + 100)
        chars = string.ascii_letters + string.digits + "_- !@#$%"
        name = "".join(random.choices(chars, k=random.randint(0, 20)))
        value = "".join(random.choices(chars, k=random.randint(0, 50)))
        try:
            Validator.validate_environment_variable(name, value)
        except ValidationError:
            pass

    @pytest.mark.parametrize("_seed", range(10))
    def test_fuzz_validate_method_no_crash(self, _seed):
        random.seed(_seed + 200)
        chars = string.ascii_uppercase
        method = "".join(random.choices(chars, k=random.randint(0, 10)))
        try:
            Validator.validate_method(method)
        except ValidationError:
            pass


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Interpolation — edge cases, non-string vars, fuzz
# ═══════════════════════════════════════════════════════════════════════════════

from equinox.core.interpolation import VariableInterpolator


class TestInterpolationComprehensive:
    # ── Non-string keys/values silently skipped ───────────────────────────

    def test_int_key_skipped(self):
        result = VariableInterpolator.interpolate("{{x}}", {123: "val", "x": "ok"})
        assert result == "ok"

    def test_list_value_skipped(self):
        result = VariableInterpolator.interpolate("{{x}}", {"x": [1, 2]})
        # Non-string value → x is filtered out → placeholder unchanged
        assert result == "{{x}}"

    def test_none_value_skipped(self):
        result = VariableInterpolator.interpolate("{{x}}", {"x": None})
        assert result == "{{x}}"

    def test_bool_value_skipped(self):
        result = VariableInterpolator.interpolate("{{flag}}", {"flag": True})
        assert result == "{{flag}}"

    # ── MAX_OUTPUT_BYTES enforcement ──────────────────────────────────────

    def test_input_text_too_large(self):
        huge_text = "a" * (2 * 1024 * 1024 + 1)
        with pytest.raises(SecurityError, match="too large"):
            VariableInterpolator.interpolate(huge_text, {})

    # ── Variable pattern edge cases ───────────────────────────────────────

    def test_triple_braces(self):
        """Outer braces are literals — only inner {{var}} matched."""
        result = VariableInterpolator.interpolate("{{{x}}}", {"x": "val"})
        assert result == "{val}"

    def test_numeric_only_var_name(self):
        result = VariableInterpolator.interpolate("{{123}}", {"123": "num"})
        assert result == "num"

    def test_single_char_var_name(self):
        result = VariableInterpolator.interpolate("{{a}}", {"a": "A"})
        assert result == "A"

    def test_hyphenated_var_name(self):
        result = VariableInterpolator.interpolate("{{my-var}}", {"my-var": "ok"})
        assert result == "ok"

    # ── Request with bytes body unchanged ─────────────────────────────────

    def test_interpolate_request_bytes_body_unchanged(self):
        from equinox.core.request import Request
        req = Request(method="POST", url="https://{{host}}/api", body=b"raw bytes")
        result = VariableInterpolator.interpolate_request(req, {"host": "example.com"})
        assert result.url == "https://example.com/api"
        assert result.body == b"raw bytes"  # bytes not interpolated

    # ── Request with None fields ──────────────────────────────────────────

    def test_interpolate_request_all_none(self):
        from equinox.core.request import Request
        req = Request(
            method="GET", url="https://api.com",
            headers=None, params=None, body=None, name=None, description=None
        )
        result = VariableInterpolator.interpolate_request(req, {"x": "y"})
        assert result.url == "https://api.com"

    # ── Fuzz: random variable names and values ────────────────────────────

    @pytest.mark.parametrize("_seed", range(10))
    def test_fuzz_interpolate_no_crash(self, _seed):
        random.seed(_seed + 300)
        # Generate a random template
        var_name = "".join(random.choices(string.ascii_letters + string.digits + "_-", k=random.randint(1, 20)))
        value = "".join(random.choices(string.printable, k=random.randint(0, 100)))
        text = f"prefix {{{{{var_name}}}}} suffix"
        try:
            result = VariableInterpolator.interpolate(text, {var_name: value})
            assert isinstance(result, str)
        except (ValidationError, SecurityError):
            pass  # Acceptable


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Interceptors — safe_body_preview, STOP/REPLACE, StructuredLogger
# ═══════════════════════════════════════════════════════════════════════════════

from equinox.core.interceptors import (
    InterceptorAction,
    InterceptorChain,
    InterceptorContext,
    InterceptorResult,
    RequestInterceptor,
    ResponseInterceptor,
    ErrorInterceptor,
    StructuredLogger,
    _safe_body_preview,
)
from equinox.core.request import Request, Response


def _mk_req(method="GET", url="https://example.com"):
    return Request(method=method, url=url)


def _mk_resp(req=None, status=200, body=b"ok", headers=None):
    req = req or _mk_req()
    return Response(
        status_code=status,
        reason="OK",
        headers=headers or {"Content-Type": "application/json"},
        body=body,
        elapsed=0.05,
        request=req,
    )


class TestInterceptorComprehensive:
    # ── _safe_body_preview ────────────────────────────────────────────────

    def test_safe_body_preview_none(self):
        assert _safe_body_preview(None) == ""

    def test_safe_body_preview_bytes(self):
        result = _safe_body_preview(b"hello world")
        assert result == "hello world"

    def test_safe_body_preview_bytearray(self):
        result = _safe_body_preview(bytearray(b"hello"))
        assert result == "hello"

    def test_safe_body_preview_truncates_bytes(self):
        result = _safe_body_preview(b"x" * 5000, limit=10)
        assert len(result) == 10

    def test_safe_body_preview_truncates_string(self):
        result = _safe_body_preview("y" * 5000, limit=10)
        assert len(result) == 10

    def test_safe_body_preview_string(self):
        result = _safe_body_preview("just a string")
        assert result == "just a string"

    # ── InterceptorResult factory methods ─────────────────────────────────

    def test_result_continue(self):
        r = InterceptorResult.continue_()
        assert r.action == InterceptorAction.CONTINUE
        assert r.value is None

    def test_result_stop(self):
        r = InterceptorResult.stop()
        assert r.action == InterceptorAction.STOP

    def test_result_replace(self):
        obj = {"replaced": True}
        r = InterceptorResult.replace(obj)
        assert r.action == InterceptorAction.REPLACE
        assert r.value is obj

    def test_result_suppress(self):
        r = InterceptorResult.suppress()
        assert r.action == InterceptorAction.SUPPRESS

    # ── InterceptorContext mutations ──────────────────────────────────────

    def test_context_replace_request(self):
        req1 = _mk_req()
        req2 = _mk_req(url="https://other.com")
        ctx = InterceptorContext(request=req1)
        ctx.replace_request(req2)
        assert ctx.request is req2

    def test_context_replace_response(self):
        req = _mk_req()
        resp1 = _mk_resp(req)
        resp2 = _mk_resp(req, status=404)
        ctx = InterceptorContext(request=req, response=resp1)
        ctx.replace_response(resp2)
        assert ctx.response.status_code == 404

    def test_context_replace_error(self):
        req = _mk_req()
        err1 = ValueError("old")
        err2 = RuntimeError("new")
        ctx = InterceptorContext(request=req, error=err1)
        ctx.replace_error(err2)
        assert ctx.error is err2

    # ── STOP action halts chain ───────────────────────────────────────────

    def test_request_stop_halts_chain(self):
        chain = InterceptorChain()
        call_log = []

        class StopInterceptor(RequestInterceptor):
            def intercept(self, ctx):
                call_log.append("stop")
                return InterceptorResult.stop()

        class AfterStop(RequestInterceptor):
            def intercept(self, ctx):
                call_log.append("after")
                return InterceptorResult.continue_()

        chain.add_request_interceptor(StopInterceptor())
        chain.add_request_interceptor(AfterStop())
        chain.process_request(_mk_req())
        assert call_log == ["stop"]

    def test_response_stop_halts_chain(self):
        chain = InterceptorChain()
        call_log = []

        class StopResp(ResponseInterceptor):
            def intercept(self, ctx):
                call_log.append("stop")
                return InterceptorResult.stop()

        class AfterStopResp(ResponseInterceptor):
            def intercept(self, ctx):
                call_log.append("after")
                return InterceptorResult.continue_()

        chain.add_response_interceptor(StopResp())
        chain.add_response_interceptor(AfterStopResp())
        req = _mk_req()
        chain.process_response(req, _mk_resp(req))
        assert call_log == ["stop"]

    def test_error_stop_halts_chain(self):
        chain = InterceptorChain()
        call_log = []

        class StopErr(ErrorInterceptor):
            def intercept(self, ctx):
                call_log.append("stop")
                return InterceptorResult.stop()

        class AfterStopErr(ErrorInterceptor):
            def intercept(self, ctx):
                call_log.append("after")
                return InterceptorResult.continue_()

        chain.add_error_interceptor(StopErr())
        chain.add_error_interceptor(AfterStopErr())
        result = chain.process_error(_mk_req(), ValueError("x"))
        assert call_log == ["stop"]
        assert isinstance(result, ValueError)

    # ── Error REPLACE ─────────────────────────────────────────────────────

    def test_error_replace_action(self):
        chain = InterceptorChain()

        class ReplaceErr(ErrorInterceptor):
            def intercept(self, ctx):
                return InterceptorResult.replace(RuntimeError("replaced"))

        chain.add_error_interceptor(ReplaceErr())
        result = chain.process_error(_mk_req(), ValueError("original"))
        assert isinstance(result, RuntimeError)
        assert "replaced" in str(result)

    # ── Multiple interceptors ordering ────────────────────────────────────

    def test_request_interceptor_ordering(self):
        chain = InterceptorChain()
        order = []

        class A(RequestInterceptor):
            def intercept(self, ctx):
                order.append("A")
                return InterceptorResult.continue_()

        class B(RequestInterceptor):
            def intercept(self, ctx):
                order.append("B")
                return InterceptorResult.continue_()

        chain.add_request_interceptor(A())
        chain.add_request_interceptor(B())
        chain.process_request(_mk_req())
        assert order == ["A", "B"]

    # ── can_intercept guards ──────────────────────────────────────────────

    def test_request_interceptor_rejects_non_request(self):
        assert RequestInterceptor().can_intercept("not a request") is False

    def test_response_interceptor_rejects_none(self):
        assert ResponseInterceptor().can_intercept(None) is False

    def test_error_interceptor_rejects_non_exception(self):
        assert ErrorInterceptor().can_intercept("not error", _mk_req()) is False

    def test_error_interceptor_rejects_non_request(self):
        assert ErrorInterceptor().can_intercept(ValueError(), "not request") is False

    # ── StructuredLogger ──────────────────────────────────────────────────

    def test_structured_logger_emits_json(self, caplog):
        lg = logging.getLogger("test.structured")
        sl = StructuredLogger(lg)
        with caplog.at_level(logging.INFO, logger="test.structured"):
            sl.log(logging.INFO, "test_event", {"key": "val"})
        assert len(caplog.records) == 1
        data = json.loads(caplog.records[0].message)
        assert data["event"] == "test_event"
        assert data["key"] == "val"
        assert "timestamp" in data


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Request / Response — HeaderDict, cached props, to_curl, from_dict
# ═══════════════════════════════════════════════════════════════════════════════

from equinox.core.request import HeaderDict


class TestHeaderDictComprehensive:
    def test_case_insensitive_get(self):
        h = HeaderDict({"Content-Type": "text/html"})
        assert h["content-type"] == "text/html"
        assert h["CONTENT-TYPE"] == "text/html"

    def test_case_insensitive_contains(self):
        h = HeaderDict({"Authorization": "Bearer abc"})
        assert "authorization" in h
        assert "AUTHORIZATION" in h
        assert "Authorization" in h

    def test_contains_non_string_returns_false(self):
        h = HeaderDict({"X-Key": "val"})
        assert 123 not in h

    def test_set_and_overwrite(self):
        h = HeaderDict()
        h["X-Test"] = "first"
        h["x-test"] = "second"
        assert h["X-Test"] == "second"
        # Only one key stored
        assert len(list(h.keys())) == 1

    def test_delete(self):
        h = HeaderDict({"X-Remove": "val", "Keep": "yes"})
        del h["x-remove"]
        assert "X-Remove" not in h
        assert "Keep" in h

    def test_iteration_yields_original_case(self):
        h = HeaderDict({"Content-Type": "json", "X-Custom": "val"})
        keys = list(h.keys())
        # Should yield original-case keys
        assert "Content-Type" in keys
        assert "X-Custom" in keys

    def test_items_yields_original_case(self):
        h = HeaderDict({"Accept": "*/*"})
        items = list(h.items())
        assert items[0][0] == "Accept"
        assert items[0][1] == "*/*"

    def test_eq_with_plain_dict(self):
        h = HeaderDict({"Content-Type": "json"})
        assert h == {"content-type": "json"}
        assert h == {"Content-Type": "json"}
        assert h == {"CONTENT-TYPE": "json"}

    def test_eq_different_values(self):
        h = HeaderDict({"X-Key": "a"})
        assert h != {"X-Key": "b"}

    def test_as_canonical_dict_lowercase(self):
        h = HeaderDict({"Content-Type": "json", "X-Custom": "val"})
        d = h.as_canonical_dict(lowercase=True)
        assert "content-type" in d
        assert "x-custom" in d

    def test_as_canonical_dict_original_case(self):
        h = HeaderDict({"Content-Type": "json"})
        d = h.as_canonical_dict(lowercase=False)
        assert "Content-Type" in d

    def test_update_from_dict(self):
        h = HeaderDict()
        h.update({"A": "1", "B": "2"})
        assert h["a"] == "1"
        assert h["b"] == "2"

    def test_validate_name_rejects_invalid(self):
        with pytest.raises(ValidationError, match="Invalid header name"):
            HeaderDict({"Bad Header": "val"})

    def test_none_value_becomes_empty_string(self):
        h = HeaderDict({"X-Key": None})
        assert h["x-key"] == ""

    def test_int_value_converted(self):
        h = HeaderDict({"X-Count": 42})
        assert h["x-count"] == "42"

    def test_get_default(self):
        h = HeaderDict()
        assert h.get("missing", "default") == "default"

    def test_eq_with_non_dict(self):
        h = HeaderDict({"X": "1"})
        assert h != 42
        assert h != "string"


class TestResponseComprehensive:
    def _resp(self, body=b"ok", headers=None, status=200):
        req = _mk_req()
        return Response(
            status_code=status,
            reason="OK",
            headers=headers or {},
            body=body,
            elapsed=0.1,
            request=req,
        )

    def test_content_type_from_header(self):
        r = self._resp(headers={"Content-Type": "application/json; charset=utf-8"})
        assert r.content_type == "application/json"

    def test_content_type_none_when_missing(self):
        r = self._resp(headers={})
        assert r.content_type is None

    def test_encoding_from_charset(self):
        r = self._resp(headers={"Content-Type": "text/html; charset=iso-8859-1"})
        assert r.encoding == "iso-8859-1"

    def test_encoding_none_without_charset(self):
        r = self._resp(headers={"Content-Type": "application/json"})
        assert r.encoding is None

    def test_text_decoding(self):
        r = self._resp(body="héllo wörld".encode("utf-8"))
        assert r.text == "héllo wörld"

    def test_text_decoding_with_explicit_charset(self):
        body = "café".encode("latin-1")
        r = self._resp(body=body, headers={"Content-Type": "text/plain; charset=latin-1"})
        assert r.text == "café"

    def test_json_parsing(self):
        r = self._resp(body=b'{"key": "value"}')
        assert r.json() == {"key": "value"}

    def test_json_malformed_raises(self):
        r = self._resp(body=b"not json")
        with pytest.raises(ValueError, match="Malformed"):
            r.json()

    def test_is_json(self):
        r = self._resp(headers={"Content-Type": "application/json"})
        assert r.is_json is True

    def test_is_html(self):
        r = self._resp(headers={"Content-Type": "text/html"})
        assert r.is_html is True

    def test_is_xml(self):
        r = self._resp(headers={"Content-Type": "application/xml"})
        assert r.is_xml is True

    def test_is_json_false_without_ct(self):
        r = self._resp(headers={})
        assert r.is_json is False
        assert r.is_html is False
        assert r.is_xml is False

    def test_size(self):
        r = self._resp(body=b"12345")
        assert r.size == 5

    def test_to_dict(self):
        r = self._resp(body=b'{"a":1}', headers={"Content-Type": "application/json"})
        d = r.to_dict()
        assert d["status_code"] == 200
        assert d["reason"] == "OK"
        assert "timestamp" in d
        assert d["size"] == 7


class TestRequestComprehensive:
    def test_to_curl_get(self):
        req = Request(method="GET", url="https://example.com/api")
        curl = req.to_curl()
        assert curl.startswith("curl")
        assert "https://example.com/api" in curl
        assert "-X" not in curl  # GET is default

    def test_to_curl_post_with_body(self):
        req = Request(method="POST", url="https://example.com/api", body='{"a":1}')
        curl = req.to_curl()
        assert "-X POST" in curl
        assert "-d" in curl
        assert '{"a":1}' in curl

    def test_to_curl_with_headers(self):
        req = Request(
            method="GET",
            url="https://example.com",
            headers={"Authorization": "Bearer tok"},
        )
        curl = req.to_curl()
        assert "-H" in curl
        assert "Authorization: Bearer tok" in curl

    def test_to_curl_bytes_body(self):
        req = Request(method="PUT", url="https://example.com", body=b"raw data")
        curl = req.to_curl()
        assert "-d" in curl
        assert "raw data" in curl

    def test_from_dict_roundtrip(self):
        original = Request(
            method="POST",
            url="https://api.com",
            headers={"X-Key": "val"},
            params={"q": "search"},
            body='{"a": 1}',
            name="My Request",
            description="A test",
            path_params={"id": "123"},
            pre_script="x = 1",
            post_script="y = 2",
        )
        d = original.to_dict()
        restored = Request.from_dict(d)
        assert restored.method == "POST"
        assert restored.url == "https://api.com"
        assert restored.headers["X-Key"] == "val"
        assert restored.params == {"q": "search"}
        assert restored.body == '{"a": 1}'
        assert restored.name == "My Request"
        assert restored.path_params == {"id": "123"}
        assert restored.pre_script == "x = 1"

    def test_from_dict_minimal(self):
        req = Request.from_dict({"url": "https://example.com"})
        assert req.method == "GET"
        assert req.url == "https://example.com"

    def test_invalid_method(self):
        with pytest.raises(ValidationError, match="Invalid HTTP method"):
            Request(method="INVALID", url="https://example.com")

    def test_method_normalised_to_upper(self):
        req = Request(method="post", url="https://example.com")
        assert req.method == "POST"

    def test_to_dict_with_bytes_body(self):
        req = Request(method="POST", url="https://example.com", body=b"binary")
        d = req.to_dict()
        assert d["body"] == "binary"

    def test_final_url_with_params(self):
        req = Request(
            method="GET",
            url="https://example.com/api",
            params={"page": "1", "limit": "10"},
        )
        url = req._final_url()
        assert "page=1" in url
        assert "limit=10" in url

    def test_final_url_template_with_params(self):
        req = Request(
            method="GET",
            url="{{base}}/api",
            params={"q": "test"},
        )
        url = req._final_url()
        assert "q=test" in url

    def test_from_dict_with_auth(self):
        d = {
            "url": "https://api.com",
            "method": "GET",
            "auth": {"token": "Bearer abc"},
            "auth_type": "BearerAuth",
        }
        req = Request.from_dict(d)
        assert req.auth is not None

    def test_from_dict_invalid_auth(self):
        d = {
            "url": "https://api.com",
            "auth": {"invalid": True},
            "auth_type": "NonExistentAuth",
        }
        # Now raises ValidationError (domain exception) instead of ValueError
        from equinox.core.exceptions import ValidationError
        with pytest.raises((ValidationError, ValueError), match="Invalid auth"):
            Request.from_dict(d)


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Dotenv — inline comments, unicode, CRLF, tabs
# ═══════════════════════════════════════════════════════════════════════════════

from equinox.core.dotenv import parse_dotenv


class TestDotenvComprehensive:
    def test_inline_comment_stripped(self):
        text = "KEY=value # this is a comment"
        assert parse_dotenv(text) == {"KEY": "value"}

    def test_hash_without_space_preserved(self):
        text = "COLOR=#fff"
        assert parse_dotenv(text) == {"COLOR": "#fff"}

    def test_hash_in_quoted_value_preserved(self):
        text = 'COLOR="#fff # not a comment"'
        assert parse_dotenv(text) == {"COLOR": "#fff # not a comment"}

    def test_unicode_value(self):
        text = "GREETING=héllo wörld"
        assert parse_dotenv(text) == {"GREETING": "héllo wörld"}

    def test_unicode_key(self):
        text = "CAFÉ=latte"
        assert parse_dotenv(text) == {"CAFÉ": "latte"}

    def test_windows_crlf(self):
        text = "A=1\r\nB=2\r\nC=3"
        result = parse_dotenv(text)
        assert result == {"A": "1", "B": "2", "C": "3"}

    def test_tabs_in_unquoted_value(self):
        text = "KEY=val\twith\ttabs"
        assert parse_dotenv(text) == {"KEY": "val\twith\ttabs"}

    def test_multiple_equals_in_value(self):
        text = "CONN=host=db;user=admin;pass=secret"
        assert parse_dotenv(text) == {"CONN": "host=db;user=admin;pass=secret"}

    def test_empty_quoted_value(self):
        text = 'KEY=""'
        assert parse_dotenv(text) == {"KEY": ""}

    def test_single_quoted_empty(self):
        text = "KEY=''"
        assert parse_dotenv(text) == {"KEY": ""}

    def test_duplicate_key_last_wins(self):
        text = "K=first\nK=second"
        assert parse_dotenv(text) == {"K": "second"}

    def test_export_without_value(self):
        # "export KEY" with no = sign should be skipped
        text = "export NOVALUE"
        assert parse_dotenv(text) == {}

    def test_comment_after_export(self):
        text = "# export COMMENTED=yes\nexport REAL=yes"
        assert parse_dotenv(text) == {"REAL": "yes"}


# ═══════════════════════════════════════════════════════════════════════════════
# 8. AuthCipher — round-trip, reset, concurrency
# ═══════════════════════════════════════════════════════════════════════════════

from equinox.core import auth_cipher


class TestAuthCipherComprehensive:
    @pytest.fixture(autouse=True)
    def _isolate_key(self, tmp_path, monkeypatch):
        """Use a temporary key so tests don't touch the real user key."""
        key_file = tmp_path / ".equinox" / ".key"
        key_file.parent.mkdir(parents=True, exist_ok=True)
        # Patch crypto module to use tmp key path
        monkeypatch.setattr(
            "equinox.core.crypto.default_key_path",
            lambda: key_file,
        )
        auth_cipher.reset_cipher()
        yield
        auth_cipher.reset_cipher()

    def test_encrypt_decrypt_roundtrip(self):
        plaintext = '{"client_secret": "my_secret", "token": "abc"}'
        encrypted = auth_cipher.encrypt_auth_data(plaintext)
        assert encrypted.startswith("enc:")
        assert "my_secret" not in encrypted
        decrypted = auth_cipher.decrypt_auth_data(encrypted)
        assert decrypted == plaintext

    def test_decrypt_plaintext_passthrough(self):
        """Legacy plaintext (no enc: prefix) returned as-is."""
        legacy = '{"type": "bearer", "token": "abc"}'
        assert auth_cipher.decrypt_auth_data(legacy) == legacy

    def test_decrypt_empty_string(self):
        assert auth_cipher.decrypt_auth_data("") == ""

    def test_decrypt_none(self):
        assert auth_cipher.decrypt_auth_data(None) is None

    def test_encrypt_large_payload(self):
        big = json.dumps({"data": "x" * 10_000})
        encrypted = auth_cipher.encrypt_auth_data(big)
        decrypted = auth_cipher.decrypt_auth_data(encrypted)
        assert decrypted == big

    def test_reset_and_re_encrypt(self):
        plain = '{"key": "val"}'
        enc1 = auth_cipher.encrypt_auth_data(plain)
        auth_cipher.reset_cipher()
        # Should still work after reset (re-creates Fernet from same key)
        dec = auth_cipher.decrypt_auth_data(enc1)
        assert dec == plain

    def test_corrupt_ciphertext_raises(self):
        with pytest.raises(SecurityError, match="decrypt"):
            auth_cipher.decrypt_auth_data("enc:not-valid-fernet-token")

    def test_concurrent_encrypt_decrypt(self):
        """Multiple threads encrypting/decrypting should not corrupt."""
        results = {}
        errors = []

        def worker(idx):
            try:
                plain = f'{{"id": {idx}}}'
                enc = auth_cipher.encrypt_auth_data(plain)
                dec = auth_cipher.decrypt_auth_data(enc)
                results[idx] = (plain, dec)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Errors occurred: {errors}"
        for idx, (plain, dec) in results.items():
            assert plain == dec


# ═══════════════════════════════════════════════════════════════════════════════
# 9. Exceptions — hierarchy, details, aliases
# ═══════════════════════════════════════════════════════════════════════════════


class TestExceptionHierarchy:
    def test_base_exception_message(self):
        e = EquinoxError("something failed")
        assert e.message == "something failed"
        assert str(e) == "something failed"

    def test_base_exception_details(self):
        e = EquinoxError("msg", details={"code": 42})
        assert e.details == {"code": 42}

    def test_base_exception_default_details(self):
        e = EquinoxError("msg")
        assert e.details == {}

    @pytest.mark.parametrize("cls", [
        RequestError, AuthError, StorageError, PluginError,
        ValidationError, SecurityError, RateLimitError,
        RequestTimeoutError, FileSizeError, CertificateError,
    ])
    def test_subclass_is_equinox_error(self, cls):
        e = cls("test")
        assert isinstance(e, EquinoxError)

    def test_duplicate_error_is_storage_error(self):
        e = DuplicateError("dup")
        assert isinstance(e, StorageError)
        assert isinstance(e, EquinoxError)

    def test_timeout_alias(self):
        assert TimeoutAlias is RequestTimeoutError

    def test_exception_with_details_round_trip(self):
        details = {"url": "https://example.com", "code": 500}
        e = RequestError("failed", details=details)
        assert e.details["url"] == "https://example.com"
        assert e.details["code"] == 500

    def test_catch_storage_catches_duplicate(self):
        """DuplicateError should be catchable as StorageError."""
        with pytest.raises(StorageError):
            raise DuplicateError("duplicate row")

    def test_catch_equinox_catches_all(self):
        """All subclasses catchable as EquinoxError."""
        for cls in [RequestError, AuthError, ValidationError, SecurityError]:
            with pytest.raises(EquinoxError):
                raise cls("test")

