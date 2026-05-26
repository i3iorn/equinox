"""Send, auth, and helper mixin classes for RequestPanel.

This package splits the request-panel mixin logic into focused modules:

- ``_constants``   — shared constants, dispatch tables, compiled patterns
- ``_helpers``     — module-level helper functions (logging-panel notifications)
- ``_send_mixin``  — ``_RequestSendMixin`` (dispatch, response, scripts, captures)
- ``_auth_mixin``  — ``_RequestAuthMixin`` (auth config, display, inheritance)

All public names are re-exported here for backward compatibility — existing
imports like ``from equinox.gui.request_panel.mixins import _RequestSendMixin``
continue to work unchanged.
"""

from equinox.gui.request_panel._mixins.auth_mixin import _RequestAuthMixin
from equinox.gui.request_panel._mixins.helpers import notify_log_panel
from equinox.gui.request_panel._mixins.send_mixin import _RequestSendMixin

__all__ = [
    "_RequestSendMixin",
    "_RequestAuthMixin",
    "notify_log_panel",
]
