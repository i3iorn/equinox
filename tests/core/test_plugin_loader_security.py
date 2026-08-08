from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from equinox.core.request import Request
from equinox.plugins.base import PluginContext
from equinox.plugins.manager import PluginManager
from equinox.plugins.security import SecurePluginContext


def _write_plugin(
    root: Path,
    plugin_dir_name: str,
    plugin_module: str,
    manifest_overrides: dict[str, Any] | None = None,
    extra_files: dict[str, str] | None = None,
) -> Path:
    plugin_dir = root / plugin_dir_name
    plugin_dir.mkdir(parents=True, exist_ok=True)

    plugin_file = plugin_dir / "plugin.py"
    plugin_file.write_text(plugin_module, encoding="utf-8")

    digest = hashlib.sha256(plugin_file.read_bytes()).hexdigest()
    manifest: dict[str, Any] = {
        "name": f"plugin_{plugin_dir_name}",
        "version": "1.0.0",
        "author": "tests",
        "main": "plugin.py",
        "checksum": digest,
    }
    if manifest_overrides:
        for key, value in manifest_overrides.items():
            manifest[key] = value

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


def test_plugin_manager_strict_checksum_mode_requires_manifest_checksum(
    tmp_path: Path,
    monkeypatch,
) -> None:
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

    monkeypatch.setenv("EQUINOX_REQUIRE_PLUGIN_CHECKSUMS", "1")
    manager = PluginManager(str(tmp_path), _manager_context())

    assert manager.plugins == []


def test_plugin_manager_deny_by_default_requires_allowlist(tmp_path: Path, monkeypatch) -> None:
    _write_plugin(
        tmp_path,
        "deny_default_plugin",
        """
from equinox.plugins.base import Plugin

class PluginClass(Plugin):
    @property
    def name(self):
        return "deny-default"

    @property
    def version(self):
        return "1.0"
""".strip(),
    )

    monkeypatch.setenv("EQUINOX_PLUGIN_DENY_BY_DEFAULT", "1")
    manager = PluginManager(str(tmp_path), _manager_context())
    assert manager.plugins == []


def test_plugin_manager_deny_by_default_allows_allowlisted_plugin(
    tmp_path: Path,
    monkeypatch,
) -> None:
    plugin_dir = _write_plugin(
        tmp_path,
        "allowlisted_plugin",
        """
from equinox.plugins.base import Plugin

class PluginClass(Plugin):
    @property
    def name(self):
        return "allowlisted"

    @property
    def version(self):
        return "1.0"
""".strip(),
    )

    manifest = json.loads((plugin_dir / "manifest.json").read_text(encoding="utf-8"))
    allowlist = {
        "plugins": [
            {
                "name": manifest["name"],
                "version": manifest["version"],
                "checksum": manifest["checksum"],
            },
        ],
    }
    allowlist_path = tmp_path / "allowlist.json"
    allowlist_path.write_text(json.dumps(allowlist), encoding="utf-8")

    monkeypatch.setenv("EQUINOX_PLUGIN_DENY_BY_DEFAULT", "1")
    monkeypatch.setenv("EQUINOX_PLUGIN_ALLOWLIST_FILE", str(allowlist_path))
    manager = PluginManager(str(tmp_path), _manager_context())
    assert len(manager.plugins) == 1


def test_plugin_manager_rejects_dangerous_permissions_without_opt_in(tmp_path: Path) -> None:
    _write_plugin(
        tmp_path,
        "dangerous_permission_plugin",
        """
from equinox.plugins.base import Plugin

class PluginClass(Plugin):
    @property
    def name(self):
        return "dangerous"

    @property
    def version(self):
        return "1.0"
""".strip(),
        manifest_overrides={"permissions": ["system.execute"]},
    )

    manager = PluginManager(str(tmp_path), _manager_context())
    assert manager.plugins == []


def test_plugin_manager_allows_dangerous_permissions_with_explicit_opt_in(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_plugin(
        tmp_path,
        "dangerous_permission_optin_plugin",
        """
from equinox.plugins.base import Plugin

class PluginClass(Plugin):
    @property
    def name(self):
        return "dangerous-optin"

    @property
    def version(self):
        return "1.0"
""".strip(),
        manifest_overrides={"permissions": ["system.execute"]},
    )

    monkeypatch.setenv("EQUINOX_ALLOW_DANGEROUS_PLUGIN_PERMS", "1")
    manager = PluginManager(str(tmp_path), _manager_context())
    assert len(manager.plugins) == 1


def test_plugin_manifest_unknown_permission_is_denied(tmp_path: Path) -> None:
    _write_plugin(
        tmp_path,
        "unknown_permission_plugin",
        """
from equinox.plugins.base import Plugin

class PluginClass(Plugin):
    @property
    def name(self):
        return "unknown-perm"

    @property
    def version(self):
        return "1.0"
""".strip(),
        manifest_overrides={"permissions": ["network.impossible"]},
    )

    manager = PluginManager(str(tmp_path), _manager_context())
    assert manager.plugins == []


def test_plugin_hook_failures_emit_consistent_audit_events(tmp_path: Path, monkeypatch) -> None:
    _write_plugin(
        tmp_path,
        "hook_error_plugin",
        """
from equinox.plugins.base import Plugin

class PluginClass(Plugin):
    @property
    def name(self):
        return "hook-error"

    @property
    def version(self):
        return "1.0"

    def on_request(self, request):
        raise RuntimeError("plugin-request-failure")
""".strip(),
    )

    events = []

    class _AuditRecorder:
        def log_plugin_event(self, plugin_name, event, error=None, user=None):
            events.append({"plugin": plugin_name, "event": event, "error": error, "user": user})

    monkeypatch.setattr("equinox.plugins.manager._audit", _AuditRecorder())

    manager = PluginManager(str(tmp_path), _manager_context())
    manager.process_request(Request(method="GET", url="https://example.com"))

    assert any(evt["event"] == "hook_error" for evt in events)
    assert any("on_request" in (evt.get("error") or "") for evt in events)
