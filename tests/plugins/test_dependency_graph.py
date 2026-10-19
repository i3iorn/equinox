"""Tests for validate_plugin_dependency_graph traversal and escape detection."""

from __future__ import annotations

from pathlib import Path

import pytest

from equinox.core.exceptions import SecurityError
from equinox.plugins.security import validate_plugin_dependency_graph


def _write(root: Path, rel: str, content: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


class TestDependencyGraphTraversal:
    def test_single_safe_file(self, tmp_path: Path) -> None:
        entry = _write(tmp_path, "plugin.py", "import json\nprint('ok')\n")
        visited = validate_plugin_dependency_graph(entry, tmp_path)
        assert entry in visited

    def test_local_import_is_visited(self, tmp_path: Path) -> None:
        _write(tmp_path, "helper.py", "VALUE = 42\n")
        entry = _write(tmp_path, "plugin.py", "import helper\nprint(helper.VALUE)\n")
        visited = validate_plugin_dependency_graph(entry, tmp_path)
        assert len(visited) == 2

    def test_local_from_import_is_visited(self, tmp_path: Path) -> None:
        _write(tmp_path, "tools.py", "def util(): pass\n")
        entry = _write(tmp_path, "plugin.py", "from tools import util\n")
        visited = validate_plugin_dependency_graph(entry, tmp_path)
        assert len(visited) == 2

    def test_package_init_visited(self, tmp_path: Path) -> None:
        _write(tmp_path, "mypkg/__init__.py", "X = 1\n")
        _write(tmp_path, "mypkg/sub.py", "Y = 2\n")
        entry = _write(tmp_path, "plugin.py", "from mypkg import sub\n")
        visited = validate_plugin_dependency_graph(entry, tmp_path)
        assert len(visited) >= 2  # plugin.py + at least one mypkg file

    def test_wildcard_import_scans_package(self, tmp_path: Path) -> None:
        _write(tmp_path, "helpers/__init__.py", "SAFE = 1\n")
        _write(tmp_path, "helpers/mod.py", "X = 2\n")
        entry = _write(tmp_path, "plugin.py", "from helpers import *\n")
        visited = validate_plugin_dependency_graph(entry, tmp_path)
        assert len(visited) >= 2


class TestDependencyGraphEscapeDetection:
    def test_relative_import_outside_root_is_silently_ignored(self, tmp_path: Path) -> None:
        """Relative imports resolving outside the plugin root are filtered
        by _iter_local_import_targets and never validated."""
        outer = tmp_path / "outside.py"
        outer.write_text("EVIL = True\n", encoding="utf-8")
        sub = tmp_path / "sub"
        sub.mkdir()
        entry = _write(sub, "plugin.py", "from .. import outside\n")
        visited = validate_plugin_dependency_graph(entry, sub)
        assert len(visited) == 1  # only the entry file

    def test_absolute_import_outside_root_filtered(self, tmp_path: Path) -> None:
        """Absolute imports resolving to files within root are validated."""
        outside = tmp_path / "outside.py"
        outside.write_text("EVIL = True\n", encoding="utf-8")
        entry = _write(tmp_path, "plugin.py", "import outside\n")
        visited = validate_plugin_dependency_graph(entry, tmp_path)
        assert outside in visited

    def test_absolute_import_nonexistent_module_ignored(self, tmp_path: Path) -> None:
        """Absolute imports to non-existent local modules are silently ignored."""
        entry = _write(tmp_path, "plugin.py", "import nonexistent_module_xyz\n")
        visited = validate_plugin_dependency_graph(entry, tmp_path)
        assert len(visited) == 1


class TestDependencyGraphUnsafeDependency:
    def test_local_import_with_forbidden_module_is_rejected(self, tmp_path: Path) -> None:
        _write(tmp_path, "unsafe_helper.py", "import subprocess\nVALUE = 1\n")
        entry = _write(tmp_path, "plugin.py", "import unsafe_helper\n")
        with pytest.raises(SecurityError):
            validate_plugin_dependency_graph(entry, tmp_path)


class TestDependencyGraphSyntaxErrors:
    def test_syntax_error_in_dependency_raises(self, tmp_path: Path) -> None:
        _write(tmp_path, "bad_dep.py", "def foo(\n")
        entry = _write(tmp_path, "plugin.py", "import bad_dep\n")
        with pytest.raises(SecurityError, match="syntax errors"):
            validate_plugin_dependency_graph(entry, tmp_path)
