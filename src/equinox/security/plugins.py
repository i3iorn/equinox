"""Security-enabled plugin surface.

This module re-exports the PluginManifest, PluginSandbox and validate_plugin_file
from the plugins/security implementation to provide a single import path for
policy-enforced plugin handling.
"""

from __future__ import annotations

# Re-export external plugin interfaces behind the security namespace
from equinox.plugins.security import PluginManifest as _PluginManifest  # type: ignore
from equinox.plugins.security import PluginSandbox as _PluginSandbox  # type: ignore
from equinox.plugins.security import SecurePluginContext as _SecurePluginContext  # type: ignore
from equinox.plugins.security import validate_plugin_file as _validate_plugin_file  # type: ignore
from equinox.plugins.security import validate_plugin_dependency_graph as _validate_plugin_dependency_graph  # type: ignore
from equinox.plugins.security import verify_checksum as _verify_checksum  # type: ignore

# Public API
PluginManifest = _PluginManifest
PluginSandbox = _PluginSandbox
SecurePluginContext = _SecurePluginContext
validate_plugin_file = _validate_plugin_file
validate_plugin_dependency_graph = _validate_plugin_dependency_graph
verify_checksum = _verify_checksum

__all__ = [
	"PluginManifest",
	"PluginSandbox",
	"SecurePluginContext",
	"validate_plugin_file",
	"validate_plugin_dependency_graph",
	"verify_checksum",
]
