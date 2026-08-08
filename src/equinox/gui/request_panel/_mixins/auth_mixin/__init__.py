"""Compatibility aggregator for focused auth-related request-panel mixins."""

from .auth_config_mixin import AuthConfigMixin
from .auth_display_mixin import AuthDisplayMixin


class _RequestAuthMixin(AuthConfigMixin, AuthDisplayMixin):
    """Compose the focused auth configuration and display helpers."""
