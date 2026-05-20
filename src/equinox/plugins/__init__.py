"""Plugin system for Equinox"""

from equinox.plugins.base import Plugin, PluginContext
from equinox.plugins.manager import PluginManager

__all__ = ["PluginManager", "Plugin", "PluginContext"]
