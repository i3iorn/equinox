"""Tests for the sandboxed ScriptRunner."""

import pytest
from equinox.core.scripts import ScriptRunner, ScriptResult


# ── Pre-request script ────────────────────────────────────────────────────────

class TestPreScript:
    def test_basic_env_mutation(self):
        result = ScriptRunner.run_pre('env["token"] = "abc123"', {}, {})
        assert result.error is None
        assert result.env_changes == {"token": "abc123"}

    def test_no_change_returns_empty_env_changes(self):
        result = ScriptRunner.run_pre("x = 1 + 1", {}, {"existing": "val"})
        assert result.error is None
        assert result.env_changes == {}

    def test_only_changed_vars_returned(self):
        session = {"a": "old", "b": "keep"}
        result = ScriptRunner.run_pre('env["a"] = "new"', {}, session)
        assert result.env_changes == {"a": "new"}
        assert "b" not in result.env_changes

    def test_new_var_added(self):
        result = ScriptRunner.run_pre('env["fresh"] = "yes"', {}, {})
        assert result.env_changes["fresh"] == "yes"

    def test_request_dict_accessible(self):
        req = {"method": "GET", "url": "https://example.com"}
        result = ScriptRunner.run_pre('env["m"] = request["method"]', req, {})
        assert result.env_changes == {"m": "GET"}

    def test_syntax_error_captured(self):
        result = ScriptRunner.run_pre("def (", {}, {})
        assert result.error is not None
        assert result.env_changes == {}

    def test_runtime_error_captured(self):
        result = ScriptRunner.run_pre("1 / 0", {}, {})
        assert result.error is not None

    def test_empty_script_ok(self):
        result = ScriptRunner.run_pre("", {}, {})
        assert result.error is None
        assert result.env_changes == {}

    def test_whitespace_only_script_ok(self):
        result = ScriptRunner.run_pre("   \n  ", {}, {})
        assert result.error is None

    def test_values_coerced_to_str(self):
        result = ScriptRunner.run_pre('env["num"] = 42', {}, {})
        assert result.env_changes["num"] == "42"


# ── Post-response script ──────────────────────────────────────────────────────

class TestPostScript:
    def test_extract_from_response(self):
        resp = {"status_code": 200, "body": "", "json": {"id": 99}}
        result = ScriptRunner.run_post('env["uid"] = str(response["json"]["id"])', resp, {})
        assert result.env_changes == {"uid": "99"}

    def test_status_code_readable(self):
        resp = {"status_code": 404, "body": "", "json": None}
        result = ScriptRunner.run_post('env["sc"] = str(response["status_code"])', resp, {})
        assert result.env_changes == {"sc": "404"}

    def test_empty_script_ok(self):
        result = ScriptRunner.run_post("", {}, {})
        assert result.error is None

    def test_runtime_error_captured(self):
        result = ScriptRunner.run_post("raise ValueError('boom')", {}, {})
        assert result.error is not None
        assert "boom" in result.error


# ── Sandbox security ──────────────────────────────────────────────────────────

class TestSandbox:
    def _blocked(self, script):
        """Helper – assert script raises ImportError or produces an error result."""
        result = ScriptRunner.run_pre(script, {}, {})
        assert result.error is not None, f"Expected error for script: {script!r}"

    def test_os_blocked(self):
        self._blocked("import os")

    def test_sys_blocked(self):
        self._blocked("import sys")

    def test_subprocess_blocked(self):
        self._blocked("import subprocess")

    def test_socket_blocked(self):
        self._blocked("import socket")

    def test_urllib_request_blocked(self):
        self._blocked("import urllib.request")

    def test_from_urllib_request_blocked(self):
        self._blocked("from urllib import request")

    def test_from_urllib_parse_allowed(self):
        result = ScriptRunner.run_pre(
            "from urllib import parse; env['q'] = parse.quote('a b')",
            {},
            {},
        )
        assert result.error is None
        assert result.env_changes["q"] == "a%20b"

    def test_open_builtin_blocked(self):
        result = ScriptRunner.run_pre("open('/etc/passwd')", {}, {})
        assert result.error is not None

    def test_exec_builtin_blocked(self):
        result = ScriptRunner.run_pre("exec('import os')", {}, {})
        assert result.error is not None

    def test_eval_builtin_blocked(self):
        result = ScriptRunner.run_pre("eval('1+1')", {}, {})
        assert result.error is not None

    def test_allowed_json(self):
        result = ScriptRunner.run_pre(
            "import json; env['x'] = json.dumps({'a': 1})", {}, {}
        )
        assert result.error is None
        assert result.env_changes["x"] == '{"a": 1}'

    def test_allowed_re(self):
        result = ScriptRunner.run_pre(
            "import re; env['m'] = re.sub(r'x', 'y', 'axb')", {}, {}
        )
        assert result.error is None
        assert result.env_changes["m"] == "ayb"

    def test_allowed_base64(self):
        result = ScriptRunner.run_pre(
            "import base64; env['b'] = base64.b64encode(b'hi').decode()", {}, {}
        )
        assert result.error is None
        assert result.env_changes["b"] == "aGk="

    def test_allowed_hashlib(self):
        result = ScriptRunner.run_pre(
            "import hashlib; env['h'] = hashlib.md5(b'x').hexdigest()", {}, {}
        )
        assert result.error is None
        assert len(result.env_changes["h"]) == 32

    def test_print_silenced(self):
        # print should not raise but should produce no output (no-op)
        result = ScriptRunner.run_pre("print('hello')", {}, {})
        assert result.error is None


class TestSandboxOutputLimits:
    def test_non_string_env_key_rejected(self):
        result = ScriptRunner.run_pre("env[1] = 'x'", {}, {})
        assert result.error is not None
        assert "keys must be strings" in result.error

    def test_env_value_length_limit_enforced(self):
        script = (
            "env['k'] = 'x' * "
            f"{ScriptRunner.MAX_ENV_VALUE_LENGTH + 1}"
        )
        result = ScriptRunner.run_pre(script, {}, {})
        assert result.error is not None
        assert "value too long" in result.error

    def test_env_var_count_limit_enforced(self):
        script_lines = [f"env['k{i}'] = 'v'" for i in range(ScriptRunner.MAX_OUTPUT_VARS + 1)]
        result = ScriptRunner.run_pre("\n".join(script_lines), {}, {})
        assert result.error is not None
        assert "too many environment variables" in result.error.lower()

