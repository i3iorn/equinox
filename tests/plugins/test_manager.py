"""Integration tests for PluginManager loading, hooks, and lifecycle."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


from equinox.core.request import Request, Response
from equinox.plugins.base import Plugin, PluginContext
from equinox.plugins.manager import PluginManager


def _write_plugin(
    root: Path,
    name: str,
    code: str,
    *,
    manifest_overrides: dict[str, Any] | None = None,
    extra_files: dict[str, str] | None = None,
) -> Path:
    plugin_dir = root / name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    plugin_file = plugin_dir / "plugin.py"
    plugin_file.write_text(code.strip(), encoding="utf-8")
    digest = hashlib.sha256(plugin_file.read_bytes()).hexdigest()
    manifest: dict[str, Any] = {
        "name": name,
        "version": "1.0.0",
        "author": "tests",
        "main": "plugin.py",
        "checksum": digest,
    }
    if manifest_overrides:
        manifest.update(manifest_overrides)
    (plugin_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    for rel, content in (extra_files or {}).items():
        p = plugin_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return plugin_dir


SAFE_PLUGIN = """\
from equinox.plugins.base import Plugin

class PluginClass(Plugin):
    @property
    def name(self):
        return "safe"

    @property
    def version(self):
        return "1.0"
"""


def _ctx() -> PluginContext:
    return PluginContext(storage=object(), http_client=object(), config={"k": "v"})


# ── Basic loading ────────────────────────────────────────────────────────────


class TestManagerLoading:
    def test_loads_valid_plugin(self, tmp_path: Path) -> None:
        _write_plugin(tmp_path, "ok", SAFE_PLUGIN)
        mgr = PluginManager(str(tmp_path), _ctx())
        assert len(mgr.plugins) == 1
        assert mgr.plugins[0].name == "safe"

    def test_empty_dir_loads_nothing(self, tmp_path: Path) -> None:
        mgr = PluginManager(str(tmp_path / "nonexistent"), _ctx())
        assert mgr.plugins == []

    def test_missing_manifest_skipped(self, tmp_path: Path) -> None:
        d = tmp_path / "no_manifest"
        d.mkdir()
        (d / "plugin.py").write_text("x=1\n", encoding="utf-8")
        mgr = PluginManager(str(tmp_path), _ctx())
        assert mgr.plugins == []

    def test_non_plugin_class_rejected(self, tmp_path: Path) -> None:
        _write_plugin(
            tmp_path,
            "bad_class",
            """
class PluginClass:
    pass
""",
            manifest_overrides={"checksum": None},
        )
        mgr = PluginManager(str(tmp_path), _ctx())
        assert mgr.plugins == []

    def test_no_plugin_class_rejected(self, tmp_path: Path) -> None:
        _write_plugin(
            tmp_path,
            "no_plugin_class",
            "X = 42\n",
            manifest_overrides={"checksum": None},
        )
        mgr = PluginManager(str(tmp_path), _ctx())
        assert mgr.plugins == []


# ── Plugin hooks ─────────────────────────────────────────────────────────────


class TestManagerHooks:
    def _mgr_with_modify_plugin(self, tmp_path: Path) -> PluginManager:
        _write_plugin(
            tmp_path,
            "modifier",
            """
from equinox.plugins.base import Plugin
from equinox.core.request import Request, Response

class PluginClass(Plugin):
    @property
    def name(self):
        return "modifier"

    @property
    def version(self):
        return "1.0"

    def on_request(self, request):
        return Request(method="PATCH", url=request.url)

    def on_response(self, request, response):
        return Response(
            status_code=response.status_code,
            reason=response.reason,
            headers={**response.headers, "X-Plugin": "yes"},
            body=response.body,
            elapsed=response.elapsed,
            request=request,
        )

    def on_error(self, request, error):
        pass
""",
        )
        return PluginManager(str(tmp_path), _ctx())

    def test_process_request_modifies(self, tmp_path: Path) -> None:
        mgr = self._mgr_with_modify_plugin(tmp_path)
        req = Request(method="GET", url="https://example.com")
        result = mgr.process_request(req)
        assert result.method == "PATCH"

    def test_process_response_modifies(self, tmp_path: Path) -> None:
        mgr = self._mgr_with_modify_plugin(tmp_path)
        req = Request(method="GET", url="https://example.com")
        resp = Response(
            status_code=200,
            reason="OK",
            headers={},
            body=b"",
            elapsed=0.01,
            request=req,
        )
        result = mgr.process_response(req, resp)
        assert result.headers.get("X-Plugin") == "yes"

    def test_disabled_plugin_hooks_are_skipped(self, tmp_path: Path) -> None:
        _write_plugin(
            tmp_path,
            "disabler",
            """
from equinox.plugins.base import Plugin
from equinox.core.request import Request

class PluginClass(Plugin):
    @property
    def name(self):
        return "disabler"

    @property
    def version(self):
        return "1.0"

    def on_request(self, request):
        raise AssertionError("should not be called")
""",
        )
        mgr = PluginManager(str(tmp_path), _ctx())
        mgr.disable_plugin("disabler")
        req = Request(method="GET", url="https://example.com")
        result = mgr.process_request(req)
        assert result.method == "GET"


# ── Enable / disable / list / unload ─────────────────────────────────────────


class TestManagerLifecycle:
    def _named_plugin(self, name: str) -> str:
        return f"""\
from equinox.plugins.base import Plugin

class PluginClass(Plugin):
    @property
    def name(self):
        return "{name}"

    @property
    def version(self):
        return "1.0"
"""

    def test_get_plugin_found(self, tmp_path: Path) -> None:
        _write_plugin(tmp_path, "findme", self._named_plugin("findme"))
        mgr = PluginManager(str(tmp_path), _ctx())
        assert mgr.get_plugin("findme") is not None

    def test_get_plugin_not_found(self, tmp_path: Path) -> None:
        _write_plugin(tmp_path, "exists", self._named_plugin("exists"))
        mgr = PluginManager(str(tmp_path), _ctx())
        assert mgr.get_plugin("nope") is None

    def test_list_plugins(self, tmp_path: Path) -> None:
        _write_plugin(tmp_path, "listed", self._named_plugin("listed"))
        mgr = PluginManager(str(tmp_path), _ctx())
        listing = mgr.list_plugins()
        assert len(listing) == 1
        assert listing[0]["name"] == "listed"
        assert listing[0]["enabled"] is True

    def test_disable_and_enable(self, tmp_path: Path) -> None:
        _write_plugin(tmp_path, "toggle", self._named_plugin("toggle"))
        mgr = PluginManager(str(tmp_path), _ctx())
        mgr.disable_plugin("toggle")
        assert mgr.plugins[0].enabled is False
        mgr.enable_plugin("toggle")
        assert mgr.plugins[0].enabled is True

    def test_unload_all(self, tmp_path: Path) -> None:
        _write_plugin(tmp_path, "unloader", SAFE_PLUGIN)
        mgr = PluginManager(str(tmp_path), _ctx())
        assert len(mgr.plugins) == 1
        mgr.unload_all()
        assert mgr.plugins == []


# ── handle_error ─────────────────────────────────────────────────────────────


class TestManagerHandleError:
    def test_error_notifies_plugins(self, tmp_path: Path) -> None:
        errors_received: list[Exception] = []

        class RecordingPlugin(Plugin):
            @property
            def name(self) -> str:
                return "rec"

            @property
            def version(self) -> str:
                return "1.0"

            def on_error(self, request: Request, error: Exception) -> None:
                errors_received.append(error)

        # Inject a plugin directly
        _write_plugin(tmp_path, "recorder", SAFE_PLUGIN)
        mgr = PluginManager(str(tmp_path), _ctx())
        # Replace the loaded plugin with our recording one
        ctx = PluginContext(storage=None, http_client=None, config={})
        mgr.plugins = [RecordingPlugin(ctx)]
        req = Request(method="GET", url="https://example.com")
        exc = RuntimeError("boom")
        mgr.handle_error(req, exc)
        assert errors_received == [exc]

    def test_hook_failure_in_handle_error_does_not_propagate(self, tmp_path: Path) -> None:
        class BadErrorPlugin(Plugin):
            @property
            def name(self) -> str:
                return "bad"

            @property
            def version(self) -> str:
                return "1.0"

            def on_error(self, request: Request, error: Exception) -> None:
                raise AssertionError("bad plugin on_error")

        _write_plugin(tmp_path, "ok_plugin", SAFE_PLUGIN)
        mgr = PluginManager(str(tmp_path), _ctx())
        ctx = PluginContext(storage=None, http_client=None, config={})
        mgr.plugins = [BadErrorPlugin(ctx)]
        req = Request(method="GET", url="https://example.com")
        mgr.handle_error(req, RuntimeError("x"))  # should not raise
