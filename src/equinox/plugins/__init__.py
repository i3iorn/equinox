"""Plugin system for Equinox"""

from equinox.plugins.manager import PluginManager
from equinox.plugins.base import Plugin, PluginContext

__all__ = ["PluginManager", "Plugin", "PluginContext"]
