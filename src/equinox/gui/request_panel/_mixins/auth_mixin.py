"""Compatibility aggregator for focused auth-related request-panel mixins."""
from equinox.gui.request_panel._mixins.auth_config_mixin import AuthConfigMixin
from equinox.gui.request_panel._mixins.auth_display_mixin import AuthDisplayMixin


class _RequestAuthMixin(AuthConfigMixin, AuthDisplayMixin):  # type: ignore[misc]
    """Compose the focused auth configuration and display helpers."""
