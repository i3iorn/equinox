from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Dict, Optional

from equinox.plugins.base import PluginContext
from equinox.plugins.manager import PluginManager
from equinox.plugins.security import SecurePluginContext


def _write_plugin(
    root: Path,
    plugin_dir_name: str,
    plugin_module: str,
    manifest_overrides: Optional[Dict[str, object]] = None,
    extra_files: Optional[Dict[str, str]] = None,
) -> Path:
    plugin_dir = root / plugin_dir_name
    plugin_dir.mkdir(parents=True, exist_ok=True)

    plugin_file = plugin_dir / "plugin.py"
    plugin_file.write_text(plugin_module, encoding="utf-8")

    digest = hashlib.sha256(plugin_file.read_bytes()).hexdigest()
    manifest = {
        "name": f"plugin_{plugin_dir_name}",
        "version": "1.0.0",
        "author": "tests",
        "main": "plugin.py",
        "checksum": digest,
    }
    if manifest_overrides:
        manifest.update(manifest_overrides)

    (plugin_dir / "manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )

    for rel, content in (extra_files or {}).items():
        p = plugin_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    return plugin_dir


def _manager_context() -> PluginContext:
    return PluginContext(storage=object(), http_client=object(), config={"k": "v"})


def test_plugin_manager_uses_secure_context_and_valid_checksum(tmp_path: Path) -> None:
    _write_plugin(
        tmp_path,
        "ok_plugin",
        """
from equinox.plugins.base import Plugin

class PluginClass(Plugin):
    @property
    def name(self):
        return "ok"

    @property
    def version(self):
        return "1.0"
""".strip(),
    )

    manager = PluginManager(str(tmp_path), _manager_context())

    assert len(manager.plugins) == 1
    assert isinstance(manager.plugins[0].context, SecurePluginContext)


def test_plugin_manager_rejects_checksum_mismatch(tmp_path: Path) -> None:
    _write_plugin(
        tmp_path,
        "bad_checksum_plugin",
        """
from equinox.plugins.base import Plugin

class PluginClass(Plugin):
    @property
    def name(self):
        return "bad"

    @property
    def version(self):
        return "1.0"
""".strip(),
        manifest_overrides={"checksum": "not-a-real-checksum"},
    )

    manager = PluginManager(str(tmp_path), _manager_context())

    assert manager.plugins == []


def test_plugin_manager_validates_local_import_dependency_graph(tmp_path: Path) -> None:
    _write_plugin(
        tmp_path,
        "dep_plugin",
        """
import helper
from equinox.plugins.base import Plugin

class PluginClass(Plugin):
    @property
    def name(self):
        return "dep"

    @property
    def version(self):
        return "1.0"
""".strip(),
        manifest_overrides={"checksum": None},
        extra_files={
            "helper.py": "import subprocess\nVALUE = 1\n",
        },
    )

    manager = PluginManager(str(tmp_path), _manager_context())

    # helper.py contains forbidden import and should prevent loading.
    assert manager.plugins == []


def test_plugin_manager_validates_wildcard_package_imports_aggressively(tmp_path: Path) -> None:
    _write_plugin(
        tmp_path,
        "wildcard_plugin",
        """
from helpers import *
from equinox.plugins.base import Plugin

class PluginClass(Plugin):
    @property
    def name(self):
        return "wildcard"

    @property
    def version(self):
        return "1.0"
""".strip(),
        manifest_overrides={"checksum": None},
        extra_files={
            "helpers/__init__.py": "SAFE = 1\n",
            "helpers/danger.py": "import subprocess\n",
        },
    )

    manager = PluginManager(str(tmp_path), _manager_context())

    assert manager.plugins == []


def test_plugin_manager_strict_checksum_mode_requires_manifest_checksum(tmp_path: Path) -> None:
    _write_plugin(
        tmp_path,
        "strict_plugin",
        """
from equinox.plugins.base import Plugin

class PluginClass(Plugin):
    @property
    def name(self):
        return "strict"

    @property
    def version(self):
        return "1.0"
""".strip(),
        manifest_overrides={"checksum": None},
    )

    manager = PluginManager(str(tmp_path), _manager_context(), require_checksums=True)

    assert manager.plugins == []


