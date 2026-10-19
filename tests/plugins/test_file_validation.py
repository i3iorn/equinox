"""Tests for plugin file validation (AST security, extension, size, syntax)."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from equinox.core.exceptions import SecurityError
from equinox.plugins.security import (
    validate_plugin_file,
    _find_dangerous_imports,
    _find_dangerous_functions,
    _find_dangerous_os_calls,
)
import ast


# ── validate_plugin_file ─────────────────────────────────────────────────────


class TestValidatePluginFile:
    def test_valid_plugin_passes(self, tmp_path: Path) -> None:
        p = tmp_path / "ok.py"
        p.write_text("import json\nprint('hello')\n", encoding="utf-8")
        assert validate_plugin_file(p) is True

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(SecurityError, match="not found"):
            validate_plugin_file(tmp_path / "nope.py")

    def test_wrong_extension_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.txt"
        p.write_text("code", encoding="utf-8")
        with pytest.raises(SecurityError, match="Invalid plugin file type"):
            validate_plugin_file(p)

    def test_oversized_plugin_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "big.py"
        p.write_bytes(b"x = 1\n" * (1024 * 1024 // 6 + 1))
        with pytest.raises(SecurityError, match="too large"):
            validate_plugin_file(p)

    def test_syntax_error_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "bad_syntax.py"
        p.write_text("def foo(\n", encoding="utf-8")
        with pytest.raises(SecurityError, match="syntax errors"):
            validate_plugin_file(p)


# ── AST dangerous import detection ───────────────────────────────────────────


class TestDangerousImports:
    def _check(self, code: str) -> list[str]:
        tree = ast.parse(textwrap.dedent(code))
        return _find_dangerous_imports(tree)

    def test_safe_import_no_violations(self) -> None:
        assert self._check("import json\nimport os.path\n") == []

    def test_subprocess_import_detected(self) -> None:
        violations = self._check("import subprocess\n")
        assert len(violations) == 1
        assert "subprocess" in violations[0]

    def test_from_subprocess_import_detected(self) -> None:
        violations = self._check("from subprocess import Popen\n")
        assert len(violations) == 1

    def test_dotted_import_top_level_detected(self) -> None:
        violations = self._check("import ctypes.util\n")
        assert len(violations) == 1
        assert "ctypes" in violations[0]

    def test_from_dotted_module_detected(self) -> None:
        violations = self._check("from os.path import join\n")
        # os is not in the dangerous list, but os-specific forbidden calls are handled separately
        assert violations == []

    def test_multiprocessing_detected(self) -> None:
        violations = self._check("import multiprocessing\n")
        assert len(violations) == 1

    def test_socket_detected(self) -> None:
        violations = self._check("from socket import socket\n")
        assert len(violations) == 1

    def test_multiple_violations(self) -> None:
        violations = self._check("import subprocess\nimport ctypes\nimport pickle\n")
        assert len(violations) == 3


# ── AST dangerous OS call detection ──────────────────────────────────────────


class TestDangerousOsCalls:
    def _check(self, code: str) -> list[str]:
        tree = ast.parse(textwrap.dedent(code))
        return _find_dangerous_os_calls(tree)

    def test_os_system_detected(self) -> None:
        violations = self._check("import os\nos.system('ls')\n")
        assert any("os.system" in v for v in violations)

    def test_os_popen_detected(self) -> None:
        violations = self._check("import os\nos.popen('ls')\n")
        assert any("os.popen" in v for v in violations)

    def test_os_fork_detected(self) -> None:
        violations = self._check("import os\nos.fork()\n")
        assert any("os.fork" in v for v in violations)

    def test_os_path_join_not_detected(self) -> None:
        violations = self._check("import os\nos.path.join('a', 'b')\n")
        assert violations == []

    def test_safe_os_call_not_detected(self) -> None:
        violations = self._check("import os\nos.getcwd()\n")
        assert violations == []


# ── AST dangerous function detection ─────────────────────────────────────────


class TestDangerousFunctions:
    def _check(self, code: str) -> list[str]:
        tree = ast.parse(textwrap.dedent(code))
        return _find_dangerous_functions(tree)

    def test_eval_detected(self) -> None:
        violations = self._check("eval('1+1')\n")
        assert any("eval()" in v for v in violations)

    def test_exec_detected(self) -> None:
        violations = self._check("exec('code')\n")
        assert any("exec()" in v for v in violations)

    def test_compile_detected(self) -> None:
        violations = self._check("compile('x', 'f', 'eval')\n")
        assert any("compile()" in v for v in violations)

    def test_breakpoint_detected(self) -> None:
        violations = self._check("breakpoint()\n")
        assert any("breakpoint()" in v for v in violations)

    def test_dunder_import_detected(self) -> None:
        violations = self._check("__import__('os')\n")
        assert any("__import__()" in v for v in violations)

    def test_safe_function_not_detected(self) -> None:
        violations = self._check("len([1, 2, 3])\nprint('hi')\n")
        assert violations == []
