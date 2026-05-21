"""Tests for core/scripts — sandbox, validation and runner gaps."""

from __future__ import annotations

import pytest

from equinox.core.exceptions import SecurityError
from equinox.core.scripts.runner import ScriptRunner
from equinox.core.scripts.sandbox import _BLOCKED, _safe_import, get_safe_builtins
from equinox.core.scripts.validation import _validate_ast

# ── sandbox.py ───────────────────────────────────────────────────────────────


class TestSafeImport:
    def test_allowed_module_imports(self) -> None:
        mod = _safe_import("json")
        import json as _json

        assert mod is _json

    def test_disallowed_module_raises(self) -> None:
        with pytest.raises(ImportError, match="not allowed"):
            _safe_import("os")

    def test_relative_import_raises(self) -> None:
        with pytest.raises(ImportError, match="Relative imports"):
            _safe_import("json", level=1)

    def test_allowed_fromlist_resolves(self) -> None:
        # e.g. from datetime import datetime
        mod = _safe_import("datetime", fromlist=["datetime"])
        assert mod is not None

    def test_disallowed_fromlist_raises(self) -> None:
        with pytest.raises(ImportError, match="not allowed"):
            _safe_import("os", fromlist=["path"])


class TestGetSafeBuiltins:
    def test_blocked_names_not_present(self) -> None:
        """Blocked builtins should either be absent or replaced with safe versions.

        `__import__` is replaced with `_safe_import`; other blocked names like
        `open`, `exec`, `eval` etc. should not appear at all.
        """
        safe = get_safe_builtins()
        truly_blocked = _BLOCKED - {"__import__"}  # __import__ is replaced, not removed
        for name in truly_blocked:
            assert name not in safe, f"{name!r} should be blocked"
        # __import__ is present but replaced with the safe wrapper
        assert safe.get("__import__") is _safe_import

    def test_import_is_safe_import(self) -> None:
        safe = get_safe_builtins()
        assert safe["__import__"] is _safe_import

    def test_print_is_nop(self) -> None:
        safe = get_safe_builtins()
        result = safe["print"]("anything")
        assert result is None

    def test_common_builtins_present(self) -> None:
        safe = get_safe_builtins()
        for name in ("len", "range", "str", "int", "list", "dict", "sum"):
            assert name in safe, f"{name!r} should be available"


# ── validation.py ─────────────────────────────────────────────────────────────


class TestValidateAst:
    def test_valid_script_returns_module(self) -> None:
        import ast

        tree = _validate_ast("x = 1 + 2", "<test>")
        assert isinstance(tree, ast.Module)

    def test_dangerous_attr_class_raises(self) -> None:
        with pytest.raises(SecurityError, match="__class__"):
            _validate_ast("x = obj.__class__", "<test>")

    def test_dangerous_attr_mro_raises(self) -> None:
        with pytest.raises(SecurityError, match="__mro__"):
            _validate_ast("x = cls.__mro__", "<test>")

    def test_dangerous_attr_globals_raises(self) -> None:
        with pytest.raises(SecurityError, match="__globals__"):
            _validate_ast("x = fn.__globals__", "<test>")

    def test_type_3_args_raises(self) -> None:
        with pytest.raises(SecurityError, match="type\\(\\)"):
            _validate_ast("MyClass = type('MyClass', (object,), {})", "<test>")

    def test_blocked_call_setattr_raises(self) -> None:
        with pytest.raises(SecurityError, match="setattr"):
            _validate_ast("setattr(obj, 'key', val)", "<test>")

    def test_blocked_call_globals_raises(self) -> None:
        with pytest.raises(SecurityError, match="globals"):
            _validate_ast("g = globals()", "<test>")

    def test_blocked_call_vars_raises(self) -> None:
        with pytest.raises(SecurityError, match="vars"):
            _validate_ast("v = vars()", "<test>")

    def test_getattr_with_dangerous_attr_raises(self) -> None:
        with pytest.raises(SecurityError, match="__dict__"):
            _validate_ast("getattr(obj, '__dict__')", "<test>")

    def test_getattr_with_safe_attr_passes(self) -> None:
        tree = _validate_ast("getattr(obj, 'name')", "<test>")
        assert tree is not None

    def test_syntax_error_raises(self) -> None:
        with pytest.raises(SyntaxError):
            _validate_ast("def f(", "<test>")

    def test_blocked_call_delattr_raises(self) -> None:
        with pytest.raises(SecurityError, match="delattr"):
            _validate_ast("delattr(obj, 'x')", "<test>")

    def test_blocked_call_super_raises(self) -> None:
        with pytest.raises(SecurityError, match="super"):
            _validate_ast("super()", "<test>")


# ── runner.py ────────────────────────────────────────────────────────────────


class TestScriptRunnerCollectChangedEnv:
    """Unit-test _collect_changed_env without subprocess."""

    def test_new_key_is_added(self) -> None:
        result = ScriptRunner._collect_changed_env({"NEW": "val"}, {})
        assert result == {"NEW": "val"}

    def test_unchanged_key_excluded(self) -> None:
        result = ScriptRunner._collect_changed_env({"K": "v"}, {"K": "v"})
        assert "K" not in result

    def test_changed_value_included(self) -> None:
        result = ScriptRunner._collect_changed_env({"K": "new"}, {"K": "old"})
        assert result == {"K": "new"}

    def test_non_dict_returns_empty(self) -> None:
        result = ScriptRunner._collect_changed_env(None, {})
        assert result == {}

    def test_non_string_key_raises(self) -> None:
        with pytest.raises(ValueError, match="strings"):
            ScriptRunner._collect_changed_env({123: "val"}, {})

    def test_value_coerced_to_string(self) -> None:
        result = ScriptRunner._collect_changed_env({"N": 42}, {})
        assert result == {"N": "42"}

    def test_too_many_vars_raises(self) -> None:
        env = {f"KEY_{i}": "x" for i in range(ScriptRunner.MAX_OUTPUT_VARS + 1)}
        # All are new (not in session_vars)
        with pytest.raises(ValueError, match="Too many"):
            ScriptRunner._collect_changed_env(env, {})

    def test_key_too_long_raises(self) -> None:
        key = "K" * (ScriptRunner.MAX_ENV_KEY_LENGTH + 1)
        with pytest.raises(ValueError, match="key too long"):
            ScriptRunner._collect_changed_env({key: "val"}, {})

    def test_value_too_long_raises(self) -> None:
        val = "v" * (ScriptRunner.MAX_ENV_VALUE_LENGTH + 1)
        with pytest.raises(ValueError, match="value too long"):
            ScriptRunner._collect_changed_env({"K": val}, {})

    def test_total_size_limit_raises(self) -> None:
        # Use values that are under MAX_ENV_VALUE_LENGTH (4096) but together
        # fill > MAX_OUTPUT_TOTAL_BYTES (16 KB). 4 x 4000 = 16000 < 16384;
        # 5th entry pushes above the limit.
        mid_val = "x" * 3500
        env = {f"K{i}": mid_val for i in range(6)}
        with pytest.raises(ValueError, match="Total environment size"):
            ScriptRunner._collect_changed_env(env, {})


class TestScriptRunnerEmpty:
    def test_empty_script_returns_no_error(self) -> None:
        result = ScriptRunner.run_pre("", {}, {})
        assert result.error is None

    def test_whitespace_script_returns_no_error(self) -> None:
        result = ScriptRunner.run_pre("   \n  ", {}, {})
        assert result.error is None

    def test_too_long_script_returns_error(self) -> None:
        long_script = "x = 1\n" * (ScriptRunner.MAX_SOURCE_LENGTH // 6 + 10)
        result = ScriptRunner.run_pre(long_script, {}, {})
        assert result.error is not None
        assert "too long" in result.error.lower()

    def test_syntax_error_script_returns_error(self) -> None:
        result = ScriptRunner.run_pre("def f(", {}, {})
        assert result.error is not None

    def test_security_error_script_returns_error(self) -> None:
        result = ScriptRunner.run_pre("x = obj.__class__", {}, {})
        assert result.error is not None
