"""Local UI-usage tracking for future UX cleanup decisions.

Tracks high-value GUI interactions (tabs, buttons, actions), persists counts in
QSettings, and exposes a compact text snapshot that can be reviewed from the UI.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from equinox.core.util.time import utc_now
from equinox.gui.logging_utils import log_gui_event
from equinox.gui.ui_common import get_gui_settings
from PyQt6.QtCore import QObject
from PyQt6.QtCore import QSettings
from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QAbstractButton
from PyQt6.QtWidgets import QMenuBar
from PyQt6.QtWidgets import QTabWidget

logger = logging.getLogger(__name__)

_KEY_USAGE_COUNTS = "usage/ui_counts_json"
_FLUSH_INTERVAL_MS = 60_000
_MAX_ELEMENT_ID_LEN = 120
_TOP_ITEMS_DEFAULT = 20


class UIUsageTracker(QObject):
    """Collect and persist local UI interaction counters."""

    def __init__(
        self,
        *,
        settings: QSettings | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings or get_gui_settings()
        self._counts: dict[str, dict[str, Any]] = self._load_counts()
        self._dirty = False
        self._bound_buttons: set[int] = set()
        self._bound_actions: set[int] = set()
        self._bound_tab_widgets: set[int] = set()

        self._flush_timer = QTimer(self)
        self._flush_timer.setInterval(_FLUSH_INTERVAL_MS)
        self._flush_timer.timeout.connect(self.flush)
        self._flush_timer.start()

    def bind_widget_tree(self, root: QObject) -> None:
        """Bind known button and tab widgets under *root* for usage tracking."""
        self._bind_buttons(root)
        self._bind_actions_from_root(root)
        for obj in root.findChildren(QObject):
            if not isinstance(obj, QTabWidget):
                continue
            tabs: QTabWidget = obj
            context_name = tabs.objectName() or tabs.__class__.__name__
            self.bind_tab_widget(tabs, context=context_name)

    def bind_menu_bar(self, menu_bar: QMenuBar) -> None:
        """Track triggered actions from a menu bar recursively."""
        for top_action in menu_bar.actions():
            self._bind_action_recursive(top_action)

    def bind_tab_widget(self, tab_widget: QTabWidget, *, context: str) -> None:
        """Track tab switches on a QTabWidget."""
        marker = id(tab_widget)
        if marker in self._bound_tab_widgets:
            return
        self._bound_tab_widgets.add(marker)

        tab_widget.currentChanged.connect(
            lambda index, tabs=tab_widget, ctx=context: self._on_tab_changed(tabs, index, ctx),
        )

    def record(
        self,
        element_id: str,
        *,
        category: str,
        context: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Record a usage hit for a UI element."""
        normalized_id = self._normalize_element_id(element_id)
        if not normalized_id:
            return

        key = f"{category}|{context}|{normalized_id}"
        row = self._counts.get(key)
        if row is None:
            row = {
                "category": category,
                "context": context,
                "element_id": normalized_id,
                "count": 0,
                "last_used": "",
            }
            self._counts[key] = row

        row["count"] = int(row.get("count", 0)) + 1
        row["last_used"] = utc_now().isoformat()
        if metadata:
            row["last_meta"] = self._safe_metadata(metadata)
        self._dirty = True

    def get_count(self, *, category: str, context: str, element_id: str) -> int:
        """Return current usage count for a single tracked element key."""
        key = f"{category}|{context}|{self._normalize_element_id(element_id)}"
        row = self._counts.get(key)
        if row is None:
            return 0
        return max(0, int(row.get("count", 0)))

    def top_items(self, *, limit: int = _TOP_ITEMS_DEFAULT) -> list[dict[str, Any]]:
        """Return most-used tracked elements."""
        rows = list(self._counts.values())
        rows.sort(key=lambda row: int(row.get("count", 0)), reverse=True)
        return rows[: max(1, int(limit))]

    def snapshot_text(self, *, limit: int = _TOP_ITEMS_DEFAULT) -> str:
        """Render a compact human-readable usage report."""
        top = self.top_items(limit=limit)
        if not top:
            return "No UI usage has been recorded yet."

        lines = ["Top UI interactions (local profile):", ""]
        for idx, row in enumerate(top, start=1):
            lines.append(
                f"{idx:>2}. {row.get('count', 0):>4}  {row.get('category')}/{row.get('context')}"
                f"  ->  {row.get('element_id')}",
            )

        low_candidates = self.low_use_candidates(max_count=2)
        if low_candidates:
            lines.append("")
            lines.append("Low-use candidates (hide behind 'More'):")
            for item in low_candidates[:10]:
                lines.append(
                    f" - {item.get('category')}/{item.get('context')} -> {item.get('element_id')}"
                    f" (count={item.get('count', 0)})",
                )

        return "\n".join(lines)

    def low_use_candidates(self, *, max_count: int = 2) -> list[dict[str, Any]]:
        """Return low-use elements sorted from least-used upward."""
        rows = [row for row in self._counts.values() if int(row.get("count", 0)) <= max_count]
        rows.sort(key=lambda row: (int(row.get("count", 0)), str(row.get("element_id", ""))))
        return rows

    def reset(self) -> None:
        """Clear all tracked usage counters from memory and persisted settings."""
        self._counts = {}
        self._dirty = True
        self.flush()

    def flush(self) -> None:
        """Persist pending usage counters to QSettings."""
        if not self._dirty:
            return
        try:
            payload = json.dumps(self._counts, ensure_ascii=False)
            self._settings.setValue(_KEY_USAGE_COUNTS, payload)
            self._settings.sync()
            self._dirty = False
            log_gui_event(
                "ui_usage_flushed",
                {
                    "tracked_elements": len(self._counts),
                },
                level=logging.DEBUG,
            )
        except Exception:
            logger.warning("Failed to persist UI usage counters", exc_info=True)

    def _bind_buttons(self, root: QObject) -> None:
        for obj in root.findChildren(QObject):
            if not isinstance(obj, QAbstractButton):
                continue
            button: QAbstractButton = obj
            marker = id(button)
            if marker in self._bound_buttons:
                continue
            track_id = self._button_track_id(button)
            if not track_id:
                continue
            button.clicked.connect(
                lambda _checked=False, elem=track_id: self.record(
                    elem,
                    category="button",
                    context="gui",
                ),
            )
            self._bound_buttons.add(marker)

    def _bind_action_recursive(self, action: QAction) -> None:
        self._bind_action(action, context="menu_bar")
        submenu = action.menu()
        if submenu is None:
            return
        for child_action in submenu.actions():
            self._bind_action_recursive(child_action)

    def _bind_actions_from_root(self, root: QObject) -> None:
        """Bind QAction instances attached under a widget tree."""
        for obj in root.findChildren(QObject):
            if not isinstance(obj, QAction):
                continue
            self._bind_action(obj, context="panel_action")

    def _bind_action(self, action: QAction, *, context: str) -> None:
        marker = id(action)
        if marker in self._bound_actions:
            return
        track_id = self._action_track_id(action)
        if not track_id:
            return

        action.triggered.connect(
            lambda _checked=False, elem=track_id: self.record(
                elem,
                category="action",
                context=context,
            ),
        )
        self._bound_actions.add(marker)

    def _on_tab_changed(self, tabs: QTabWidget, index: int, context: str) -> None:
        if index < 0:
            return
        label = tabs.tabText(index)
        element_id = f"tab.{self._slugify(label)}"
        self.record(
            element_id,
            category="tab",
            context=context,
            metadata={"index": index, "label": label},
        )

    @staticmethod
    def _safe_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in metadata.items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                out[str(key)] = value
            else:
                out[str(key)] = str(value)
        return out

    def _load_counts(self) -> dict[str, dict[str, Any]]:
        raw = self._settings.value(_KEY_USAGE_COUNTS, "{}")
        try:
            data = json.loads(str(raw or "{}"))
        except Exception:
            logger.exception("Invalid UI usage payload in settings, resetting", exc_info=True)
            return {}

        if not isinstance(data, dict):
            return {}

        normalized: dict[str, dict[str, Any]] = {}
        for key, row in data.items():
            if not isinstance(row, dict):
                continue
            category = str(row.get("category") or "unknown")
            context = str(row.get("context") or "unknown")
            element_id = self._normalize_element_id(str(row.get("element_id") or ""))
            if not element_id:
                continue
            normalized[str(key)] = {
                "category": category,
                "context": context,
                "element_id": element_id,
                "count": max(0, int(row.get("count") or 0)),
                "last_used": str(row.get("last_used") or ""),
                "last_meta": self._safe_metadata(row.get("last_meta") or {}),
            }
        return normalized

    def _button_track_id(self, button: QAbstractButton) -> str:
        custom = button.property("usage_track_id")
        if isinstance(custom, str) and custom.strip():
            return self._normalize_element_id(custom)
        object_name = button.objectName()
        if object_name:
            return self._normalize_element_id(f"button.{object_name}")
        return ""

    def _action_track_id(self, action: QAction) -> str:
        custom = action.property("usage_track_id")
        if isinstance(custom, str) and custom.strip():
            return self._normalize_element_id(custom)

        object_name = action.objectName()
        if object_name:
            return self._normalize_element_id(f"action.{object_name}")

        raw_text = action.text() or ""
        cleaned = raw_text.replace("&", "").replace("…", "").strip()
        if not cleaned:
            return ""
        return self._normalize_element_id(f"action.{self._slugify(cleaned)}")

    @staticmethod
    def _slugify(value: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "_", (value or "").strip().lower())
        return slug.strip("_") or "unnamed"

    @staticmethod
    def _normalize_element_id(value: str) -> str:
        cleaned = (value or "").strip().replace(" ", "_")
        return cleaned[:_MAX_ELEMENT_ID_LEN]
