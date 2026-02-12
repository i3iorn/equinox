"""Plugin manager"""

import json
import importlib.util
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional

from equinox.plugins.base import Plugin, PluginContext
from equinox.core.exceptions import PluginError
from equinox.core.request import Request, Response


class PluginManager:
    """Manage plugins"""

    def __init__(self, plugin_dir: str, context: PluginContext):
        """
        Initialize plugin manager

        Args:
            plugin_dir: Directory containing plugins
            context: Plugin context
        """
        self.plugin_dir = Path(plugin_dir)
        self.context = context
        self.plugins: List[Plugin] = []
        self._load_plugins()

    def _load_plugins(self):
        """Load all plugins from plugin directory"""
        if not self.plugin_dir.exists():
            self.plugin_dir.mkdir(parents=True, exist_ok=True)
            return

        for plugin_path in self.plugin_dir.iterdir():
            if plugin_path.is_dir():
                self._load_plugin(plugin_path)

    def _load_plugin(self, plugin_path: Path):
        """
        Load a single plugin

        Args:
            plugin_path: Path to plugin directory
        """
        manifest_path = plugin_path / "manifest.json"
        if not manifest_path.exists():
            return

        try:
            # Load manifest
            with open(manifest_path, "r") as f:
                manifest = json.load(f)

            # Get plugin entry point
            main_file = manifest.get("main", "plugin.py")
            plugin_file = plugin_path / main_file

            if not plugin_file.exists():
                raise PluginError(f"Plugin entry point not found: {plugin_file}")

            # Load module
            spec = importlib.util.spec_from_file_location(manifest["name"], plugin_file)
            if spec is None or spec.loader is None:
                raise PluginError(f"Failed to load plugin: {manifest['name']}")

            module = importlib.util.module_from_spec(spec)
            sys.modules[manifest["name"]] = module
            spec.loader.exec_module(module)

            # Get plugin class
            if not hasattr(module, "PluginClass"):
                raise PluginError(f"Plugin must define 'PluginClass': {manifest['name']}")

            # Instantiate plugin
            plugin_class = getattr(module, "PluginClass")
            plugin = plugin_class(self.context)

            # Validate plugin
            if not isinstance(plugin, Plugin):
                raise PluginError(f"PluginClass must inherit from Plugin: {manifest['name']}")

            # Activate plugin
            plugin.activate()
            self.plugins.append(plugin)

            print(f"Loaded plugin: {plugin.name} v{plugin.version}")

        except Exception as e:
            print(f"Failed to load plugin {plugin_path.name}: {e}")

    def get_plugin(self, name: str) -> Optional[Plugin]:
        """Get plugin by name"""
        for plugin in self.plugins:
            if plugin.name == name:
                return plugin
        return None

    def process_request(self, request: Request) -> Request:
        """
        Process request through all plugins

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
            except Exception as e:
                print(f"Plugin {plugin.name} error in on_request: {e}")

        return request

    def process_response(self, request: Request, response: Response) -> Response:
        """
        Process response through all plugins

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
            except Exception as e:
                print(f"Plugin {plugin.name} error in on_response: {e}")

        return response

    def handle_error(self, request: Request, error: Exception):
        """
        Notify plugins of request error

        Args:
            request: Request object
            error: Exception that occurred
        """
        for plugin in self.plugins:
            if not plugin.enabled:
                continue

            try:
                plugin.on_error(request, error)
            except Exception as e:
                print(f"Plugin {plugin.name} error in on_error: {e}")

    def list_plugins(self) -> List[Dict[str, Any]]:
        """List all loaded plugins"""
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
        """Enable a plugin"""
        plugin = self.get_plugin(name)
        if plugin:
            plugin.enabled = True

    def disable_plugin(self, name: str):
        """Disable a plugin"""
        plugin = self.get_plugin(name)
        if plugin:
            plugin.enabled = False

    def unload_all(self):
        """Unload all plugins"""
        for plugin in self.plugins:
            try:
                plugin.deactivate()
            except Exception as e:
                print(f"Error deactivating plugin {plugin.name}: {e}")
        self.plugins.clear()
