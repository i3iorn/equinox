"""Plugin manager"""

import json
import importlib.util
import logging
import os
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional

from equinox.plugins.base import Plugin, PluginContext
from equinox.security.plugins import (
    PluginManifest,
    PluginSandbox,
    SecurePluginContext,
    validate_plugin_dependency_graph,
    verify_checksum,
)
from equinox.core.audit import get_audit_logger
from equinox.core.exceptions import PluginError
from equinox.core.request import Request, Response

logger = logging.getLogger(__name__)
_audit = get_audit_logger()
_STRICT_CHECKSUM_ENV = "EQUINOX_REQUIRE_PLUGIN_CHECKSUMS"


def _env_flag_enabled(name: str) -> bool:
    """Return True when environment variable *name* is set to a truthy value."""
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


class PluginManager:
    """Manage plugins"""

    def __init__(
        self,
        plugin_dir: str,
        context: PluginContext,
    ):
        """Initialize plugin manager.

        Args:
            plugin_dir: Directory containing plugins
            context: Plugin context
        """
        self.plugin_dir = Path(plugin_dir)
        self.context = context
        self.require_checksums = _env_flag_enabled(_STRICT_CHECKSUM_ENV)
        self.plugins: List[Plugin] = []
        self._load_plugins()

    def _load_plugins(self):
        """Load all plugins from plugin directory."""
        if not self.plugin_dir.exists():
            logger.debug("Plugin directory does not exist, creating: %s", self.plugin_dir)
            self.plugin_dir.mkdir(parents=True, exist_ok=True)
            return

        logger.info("Loading plugins from directory: %s", self.plugin_dir)
        plugin_count = 0
        for plugin_path in self.plugin_dir.iterdir():
            if plugin_path.is_dir():
                self._load_plugin(plugin_path)
                plugin_count += 1
        logger.info("Loaded %d plugin(s)", len(self.plugins))

    def _load_plugin(self, plugin_path: Path):
        """Load a single plugin from a directory.

        Args:
            plugin_path: Path to plugin directory
        """
        manifest_path = plugin_path / "manifest.json"
        if not manifest_path.exists():
            logger.debug("No manifest.json found in plugin directory: %s", plugin_path)
            return

        try:
            logger.debug("Loading plugin manifest from: %s", manifest_path)
            with open(manifest_path, "r") as f:
                manifest_data = json.load(f)

            plugin_manifest = PluginManifest.from_dict(manifest_data)
            logger.debug("Plugin manifest parsed: name=%s version=%s", plugin_manifest.name, plugin_manifest.version)
            
            sandbox = PluginSandbox(plugin_manifest)

            main_file = manifest_data.get("main", "plugin.py")
            plugin_file = plugin_path / main_file

            if not plugin_file.exists():
                logger.error("Plugin entry point not found: %s", plugin_file)
                raise PluginError(f"Plugin entry point not found: {plugin_file}")

            # Security: validate entry + all locally imported plugin modules.
            logger.debug("Validating plugin dependency graph: %s", plugin_file)
            validate_plugin_dependency_graph(plugin_file, plugin_path)

            # Optional integrity enforcement when manifest provides checksum,
            # or mandatory enforcement when strict mode is enabled.
            if self.require_checksums and not plugin_manifest.checksum:
                raise PluginError(
                    f"Plugin '{plugin_manifest.name}' is missing required manifest checksum"
                )
            if plugin_manifest.checksum:
                verify_checksum(plugin_file, plugin_manifest.checksum)

            spec = importlib.util.spec_from_file_location(plugin_manifest.name, plugin_file)
            if spec is None or spec.loader is None:
                logger.error("Failed to create module spec for plugin: %s", plugin_manifest.name)
                raise PluginError(f"Failed to load plugin: {plugin_manifest.name}")

            logger.debug("Loading plugin module: %s", plugin_manifest.name)
            module = importlib.util.module_from_spec(spec)
            sys.modules[plugin_manifest.name] = module

            # SECURITY: activate sandbox *before* exec_module so execution
            # time limits are enforced from the very first statement.
            sandbox.start_execution()
            try:
                spec.loader.exec_module(module)
            except Exception:
                sandbox.end_execution()
                # Remove partially-loaded module to avoid poisoning sys.modules
                sys.modules.pop(plugin_manifest.name, None)
                raise
            sandbox.end_execution()

            if not hasattr(module, "PluginClass"):
                sys.modules.pop(plugin_manifest.name, None)
                logger.error("Plugin does not define PluginClass: %s", plugin_manifest.name)
                raise PluginError(f"Plugin must define 'PluginClass': {plugin_manifest.name}")

            plugin_class = getattr(module, "PluginClass")
            secure_context = SecurePluginContext(
                sandbox=sandbox,
                storage=self.context.storage,
                http_client=self.context.http_client,
                config=self.context.config,
            )
            plugin = plugin_class(secure_context)

            if not isinstance(plugin, Plugin):
                sys.modules.pop(plugin_manifest.name, None)
                logger.error("PluginClass does not inherit from Plugin: %s", plugin_manifest.name)
                raise PluginError(f"PluginClass must inherit from Plugin: {plugin_manifest.name}")

            plugin.sandbox = sandbox
            plugin.activate()
            self.plugins.append(plugin)

            logger.info("Loaded plugin: %s v%s", plugin.name, plugin.version)
            _audit.log_plugin_event(plugin.name, "loaded")

        except Exception as exc:
            logger.warning("Failed to load plugin %s: %s", plugin_path.name, exc, exc_info=True)
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
