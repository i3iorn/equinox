"""Send, auth, and helper mixin classes for RequestPanel.

This package splits the request-panel mixin logic into focused modules:

- ``_constants``   — shared constants, dispatch tables, compiled patterns
- ``_helpers``     — module-level helper functions (history, auth persistence, logging)
- ``_send_mixin``  — ``_RequestSendMixin`` (dispatch, response, scripts, captures)
- ``_auth_mixin``  — ``_RequestAuthMixin`` (auth config, display, inheritance)

All public names are re-exported here for backward compatibility — existing
imports like ``from equinox.gui.request_panel.mixins import _RequestSendMixin``
continue to work unchanged.
"""

from equinox.gui.request_panel.mixins._send_mixin import _RequestSendMixin
from equinox.gui.request_panel.mixins._auth_mixin import _RequestAuthMixin
from equinox.gui.request_panel.mixins._helpers import (
    save_history_safe as _save_history_safe,
    write_auth_to_source as _write_auth_to_source,
    notify_log_panel as _notify_log_panel,
)

__all__ = [
    "_RequestSendMixin",
    "_RequestAuthMixin",
    "_save_history_safe",
    "_write_auth_to_source",
    "_notify_log_panel",
]
