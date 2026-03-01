"""Plugin manager"""

import json
import importlib.util
import logging
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional

from equinox.plugins.base import Plugin, PluginContext
from equinox.plugins.security import PluginManifest, PluginSandbox, validate_plugin_file
from equinox.core.audit import get_audit_logger
from equinox.core.exceptions import PluginError
from equinox.core.request import Request, Response

logger = logging.getLogger(__name__)
_audit = get_audit_logger()


class PluginManager:
    """Manage plugins"""

    def __init__(self, plugin_dir: str, context: PluginContext):
        """Initialize plugin manager.

        Args:
            plugin_dir: Directory containing plugins
            context: Plugin context
        """
        self.plugin_dir = Path(plugin_dir)
        self.context = context
        self.plugins: List[Plugin] = []
        self._load_plugins()

    def _load_plugins(self):
        """Load all plugins from plugin directory."""
        if not self.plugin_dir.exists():
            self.plugin_dir.mkdir(parents=True, exist_ok=True)
            return

        for plugin_path in self.plugin_dir.iterdir():
            if plugin_path.is_dir():
                self._load_plugin(plugin_path)

    def _load_plugin(self, plugin_path: Path):
        """Load a single plugin from a directory.

        Args:
            plugin_path: Path to plugin directory
        """
        manifest_path = plugin_path / "manifest.json"
        if not manifest_path.exists():
            return

        try:
            with open(manifest_path, "r") as f:
                manifest_data = json.load(f)

            plugin_manifest = PluginManifest.from_dict(manifest_data)
            sandbox = PluginSandbox(plugin_manifest)

            main_file = manifest_data.get("main", "plugin.py")
            plugin_file = plugin_path / main_file

            if not plugin_file.exists():
                raise PluginError(f"Plugin entry point not found: {plugin_file}")

            # Security: validate plugin source before loading
            validate_plugin_file(plugin_file)

            spec = importlib.util.spec_from_file_location(plugin_manifest.name, plugin_file)
            if spec is None or spec.loader is None:
                raise PluginError(f"Failed to load plugin: {plugin_manifest.name}")

            module = importlib.util.module_from_spec(spec)
            sys.modules[plugin_manifest.name] = module
            spec.loader.exec_module(module)

            if not hasattr(module, "PluginClass"):
                raise PluginError(f"Plugin must define 'PluginClass': {plugin_manifest.name}")

            plugin_class = getattr(module, "PluginClass")
            plugin = plugin_class(self.context)

            if not isinstance(plugin, Plugin):
                raise PluginError(f"PluginClass must inherit from Plugin: {plugin_manifest.name}")

            plugin.sandbox = sandbox
            plugin.activate()
            self.plugins.append(plugin)

            logger.info("Loaded plugin: %s v%s", plugin.name, plugin.version)
            _audit.log_plugin_event(plugin.name, "loaded")

        except Exception as exc:
            logger.warning("Failed to load plugin %s: %s", plugin_path.name, exc)
            _audit.log_plugin_event(plugin_path.name, "error", error=str(exc))

    def get_plugin(self, name: str) -> Optional[Plugin]:
        """Get plugin by name."""
        for plugin in self.plugins:
            if plugin.name == name:
                return plugin
        return None

    def process_request(self, request: Request) -> Request:
        """Process request through all enabled plugins.

        Args:
            request: Request object

        Returns:
            Processed request
        """
        for plugin in self.plugins:
            if not plugin.enabled:
                continue
            try:
                modified = plugin.on_request(request)
                if modified:
                    request = modified
            except Exception as exc:
                logger.warning("Plugin %s error in on_request: %s", plugin.name, exc)

        return request

    def process_response(self, request: Request, response: Response) -> Response:
        """Process response through all enabled plugins.

        Args:
            request: Request object
            response: Response object

        Returns:
            Processed response
        """
        for plugin in self.plugins:
            if not plugin.enabled:
                continue
            try:
                modified = plugin.on_response(request, response)
                if modified:
                    response = modified
            except Exception as exc:
                logger.warning("Plugin %s error in on_response: %s", plugin.name, exc)

        return response

    def handle_error(self, request: Request, error: Exception):
        """Notify all enabled plugins of a request error.

        Args:
            request: Request object
            error: Exception that occurred
        """
        for plugin in self.plugins:
            if not plugin.enabled:
                continue
            try:
                plugin.on_error(request, error)
            except Exception as exc:
                logger.warning("Plugin %s error in on_error: %s", plugin.name, exc)

    def list_plugins(self) -> List[Dict[str, Any]]:
        """List all loaded plugins."""
        return [
            {
                "name": plugin.name,
                "version": plugin.version,
                "description": plugin.description,
                "enabled": plugin.enabled,
            }
            for plugin in self.plugins
        ]

    def enable_plugin(self, name: str):
        """Enable a plugin."""
        plugin = self.get_plugin(name)
        if plugin:
            plugin.enabled = True

    def disable_plugin(self, name: str):
        """Disable a plugin."""
        plugin = self.get_plugin(name)
        if plugin:
            plugin.enabled = False

    def unload_all(self):
        """Deactivate and unload all plugins."""
        for plugin in self.plugins:
            try:
                plugin.deactivate()
            except Exception as exc:
                logger.warning("Error deactivating plugin %s: %s", plugin.name, exc)
        self.plugins.clear()
