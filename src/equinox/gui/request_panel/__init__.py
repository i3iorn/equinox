"""Request panel package — UI for building and sending HTTP requests.

Sub-modules
-----------
- ``panel``      – :class:`RequestPanel` widget and ``_SaveRequestDialog``
- ``mixins``     – Send/auth mixin classes used by the panel
- ``body_mixin`` – Body/captures/assertions/multipart mixin
- ``builder``    – Pure-logic helpers (``assemble_body``, ``inject_content_type``)
"""

from equinox.gui.request_panel.panel import (  # noqa: F401 – public API
    RequestPanel,
    _SaveRequestDialog,
    _HEADER_PRESETS,
    _RichError,
    _enrich_exception,
)
from equinox.gui.request_panel.mixins import _save_history_safe  # noqa: F401

__all__ = [
    "RequestPanel",
    "_SaveRequestDialog",
    "_HEADER_PRESETS",
    "_RichError",
    "_enrich_exception",
    "_save_history_safe",
]

