"""Secondary tools menu helpers for ``RequestPanel``."""
from __future__ import annotations

import logging
from typing import cast

from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QMenu
from PyQt6.QtWidgets import QWidget

logger = logging.getLogger(__name__)


class RequestToolsMixin:
    """Rank request secondary tools using local usage statistics."""

    # Populated by BottomBarMixin._create_secondary_tools_menu
    _secondary_tools_menu: QMenu
    _secondary_tool_actions: list[tuple[QAction, bool]]

    def _usage_count_for_action(self, action: QAction) -> int:
        """Return the recorded usage count for a toolbar action."""
        host = cast(QWidget, cast(object, self))
        tracker = getattr(host.window(), "_ui_usage_tracker", None)
        if tracker is None:
            return 0
        element_id = action.property("usage_track_id")
        if not isinstance(element_id, str) or not element_id.strip():
            return 0
        try:
            return int(
                tracker.get_count(
                    category="action",
                    context="panel_action",
                    element_id=element_id,
                ),
            )
        except Exception:
            logger.debug("Failed to read usage count for action '%s'", element_id, exc_info=True)
            return 0

    def _rebuild_secondary_tools_menu(self) -> None:
        """Reorder secondary tools by usage while keeping destructive actions last."""
        if not hasattr(self, "_secondary_tools_menu"):
            return
        menu = self._secondary_tools_menu
        menu.clear()
        actions = list(getattr(self, "_secondary_tool_actions", []))
        if not actions:
            return

        ranked: list[tuple[int, int, QAction]] = []
        destructive: list[tuple[int, QAction]] = []
        for index, (action, is_destructive) in enumerate(actions):
            if is_destructive:
                destructive.append((index, action))
                continue
            ranked.append((self._usage_count_for_action(action), index, action))

        ranked.sort(key=lambda item: (-item[0], item[1]))
        for _, _, action in ranked:
            menu.addAction(action)
        if destructive:
            menu.addSeparator()
            for _, action in sorted(destructive, key=lambda item: item[0]):
                menu.addAction(action)
