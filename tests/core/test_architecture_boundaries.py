"""Architecture guard tests for service and plugin boundaries."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import List


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _imports(path: Path) -> List[str]:
    tree = ast.parse(_read(path), str(path))
    found: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            found.append(module)
            for alias in node.names:
                found.append(f"{module}.{alias.name}" if module else alias.name)
    return found


def test_request_service_modules_do_not_import_qt() -> None:
    requests_dir = _repo_root() / "src" / "equinox" / "application" / "requests"
    offenders = []
    for module_path in requests_dir.glob("*.py"):
        for item in _imports(module_path):
            if item.startswith("PyQt") or item.startswith("PySide"):
                offenders.append(f"{module_path.name}: {item}")

    assert not offenders, "Qt import leak in request services:\n" + "\n".join(offenders)


def test_gui_boundary_modules_do_not_import_raw_storage_managers() -> None:
    root = _repo_root() / "src" / "equinox"
    guarded_modules = [
        root / "gui" / "request_panel" / "panel.py",
        root / "gui" / "request_panel" / "_mixins" / "save_flow_mixin.py",
        root / "gui" / "request_panel" / "_mixins" / "autosave_mixin.py",
        root / "gui" / "request_panel" / "_mixins" / "send_mixin.py",
        root / "gui" / "collection_panel" / "actions.py",
        root / "gui" / "history_panel.py",
        root / "gui" / "window" / "_history.py",
    ]
    forbidden_prefixes = (
        "equinox.storage.database",
        "equinox.storage.collections.manager",
        "equinox.storage.history.manager",
    )

    offenders = []
    for module_path in guarded_modules:
        for item in _imports(module_path):
            if any(item.startswith(prefix) for prefix in forbidden_prefixes):
                offenders.append(f"{module_path.relative_to(root.parent)}: {item}")

    assert not offenders, "Raw storage import leak in GUI module:\n" + "\n".join(offenders)


def test_collection_panel_does_not_reach_through_manager_db() -> None:
    actions_path = _repo_root() / "src" / "equinox" / "gui" / "collection_panel" / "actions.py"
    content = _read(actions_path)
    assert "mgr.db" not in content


def test_plugin_modules_do_not_claim_hard_sandbox_isolation() -> None:
    plugin_manager = _repo_root() / "src" / "equinox" / "plugins" / "manager.py"
    plugin_security = _repo_root() / "src" / "equinox" / "plugins" / "security.py"

    manager_text = _read(plugin_manager)
    security_text = _read(plugin_security)

    assert "security and sandboxing" not in security_text.lower()
    assert "execution sandboxing" not in security_text.lower()
    assert "not a hard isolation boundary" in manager_text.lower()
    assert "process-level isolation" in security_text.lower()
