"""Plugin manager for trusted local extensions.

Plugins execute in-process. Security checks here are policy guards (permissions,
checksums, allowlists, and validation), not a hard isolation boundary.
"""

import json
import importlib.util
import logging
import os
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional

from equinox.plugins.base import Plugin, PluginContext
from equinox.plugins.security import (
    Permission,
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
_ALLOWLIST_FILE_ENV = "EQUINOX_PLUGIN_ALLOWLIST_FILE"
_DENY_BY_DEFAULT_ENV = "EQUINOX_PLUGIN_DENY_BY_DEFAULT"
_ALLOW_DANGEROUS_PERMS_ENV = "EQUINOX_ALLOW_DANGEROUS_PLUGIN_PERMS"

_DANGEROUS_PERMISSIONS = frozenset({
    Permission.SYSTEM_EXECUTE,
    Permission.SYSTEM_ENV,
    Permission.FILE_DELETE,
    Permission.STORAGE_DELETE,
    Permission.CREDENTIAL_WRITE,
})


def _env_flag_enabled(name: str) -> bool:
    """Return True when environment variable *name* is set to a truthy value."""
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


class PluginManager:
    """Manage trusted local plugins with policy enforcement."""

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
        self.deny_by_default = _env_flag_enabled(_DENY_BY_DEFAULT_ENV)
        self.allow_dangerous_permissions = _env_flag_enabled(_ALLOW_DANGEROUS_PERMS_ENV)
        self.allowlist = self._load_allowlist()
        self.plugins: List[Plugin] = []
        self._load_plugins()

    def _load_allowlist(self) -> Dict[str, Any]:
        """Load plugin allowlist from JSON file path in env, or return empty policy."""
        allowlist_path = os.environ.get(_ALLOWLIST_FILE_ENV, "").strip()
        if not allowlist_path:
            return {}

        path = Path(allowlist_path)
        if not path.exists() or not path.is_file():
            raise PluginError(f"Plugin allowlist file not found: {path}")

        try:
            with path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as exc:
            raise PluginError(f"Failed to parse plugin allowlist file '{path}': {exc}") from exc

        if not isinstance(data, dict):
            raise PluginError("Plugin allowlist must be a JSON object")
        return data

    def _assert_plugin_allowed(self, manifest: PluginManifest) -> None:
        """Enforce deny-by-default allowlist policy when enabled."""
        if not self.deny_by_default:
            return

        approved = self.allowlist.get("plugins") if isinstance(self.allowlist, dict) else None
        if not isinstance(approved, list):
            raise PluginError(
                f"Deny-by-default is enabled but allowlist is missing 'plugins' list ({_ALLOWLIST_FILE_ENV})"
            )

        for entry in approved:
            if not isinstance(entry, dict):
                continue
            if entry.get("name") != manifest.name:
                continue
            version = entry.get("version")
            checksum = entry.get("checksum")
            if version and version != manifest.version:
                continue
            if checksum and checksum != manifest.checksum:
                continue
            return

        raise PluginError(
            f"Plugin '{manifest.name}' is not allowlisted while deny-by-default mode is enabled"
        )

    def _assert_permissions_allowed(self, manifest: PluginManifest) -> None:
        """Block dangerous plugin permissions unless explicitly enabled by policy."""
        if self.allow_dangerous_permissions:
            return
        blocked = sorted(
            perm.value for perm in manifest.permissions if perm in _DANGEROUS_PERMISSIONS
        )
        if blocked:
            raise PluginError(
                "Plugin requests dangerous permissions without opt-in policy: "
                + ", ".join(blocked)
            )

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

    def _log_plugin_hook_failure(
        self,
        plugin_name: str,
        hook_name: str,
        exc: Exception,
    ) -> None:
        """Emit a consistent warning + audit event for plugin hook failures."""
        error_text = str(exc) or type(exc).__name__
        logger.warning(
            "Plugin hook failure",
            extra={
                "plugin": plugin_name,
                "hook": hook_name,
                "error": error_text,
                "error_type": type(exc).__name__,
            },
        )
        _audit.log_plugin_event(
            plugin_name,
            "hook_error",
            error=f"{hook_name}: {error_text}",
        )

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
            self._assert_plugin_allowed(plugin_manifest)
            self._assert_permissions_allowed(plugin_manifest)
            
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

            # Activate policy guard before module import to apply limits from
            # the first executed statement.
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
                self._log_plugin_hook_failure(plugin.name, "on_request", exc)

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
                self._log_plugin_hook_failure(plugin.name, "on_response", exc)

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
                self._log_plugin_hook_failure(plugin.name, "on_error", exc)

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
