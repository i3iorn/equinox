"""Request panel package — UI for building and sending HTTP requests.

Sub-modules
-----------
- ``panel``       – :class:`RequestPanel` widget
- ``save_dialog`` – :class:`SaveRequestDialog` for the "Save to Collection…" flow
- ``mixins``      – Send/auth mixin classes used by the panel
- ``body_mixin``  – Body/captures/assertions/multipart mixin
- ``builder``     – Pure-logic helpers (``assemble_body``, ``inject_content_type``, ``detect_body_type``)
"""

from equinox.gui.request_panel.panel import (  # noqa: F401 – public API
    RequestPanel,
    _HEADER_PRESETS,
)
from equinox.gui.request_panel.save_dialog import SaveRequestDialog  # noqa: F401

__all__ = [
    "RequestPanel",
    "SaveRequestDialog",
    "_HEADER_PRESETS",
]
