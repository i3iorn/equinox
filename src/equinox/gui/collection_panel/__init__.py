"""Collections panel package — tree view for managing collections and requests.

Sub-modules
-----------
- ``panel``   – :class:`CollectionsPanel` widget and ``_NewRequestDialog``
- ``actions`` – Context-menu / keyboard action handlers (mixin)
"""

from equinox.gui.collection_panel.panel import (  # noqa: F401 – public API
    CollectionsPanel,
    _NewRequestDialog,
)

__all__ = [
    "CollectionsPanel",
    "_NewRequestDialog",
]

