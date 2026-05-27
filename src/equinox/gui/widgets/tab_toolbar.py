"""Reusable row-management toolbar widget.

Provides a consistent left-aligned label with standard Add / Remove /
Enable All / Disable All actions and optional presets or file-browse support.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Protocol

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout
from PyQt6.QtWidgets import QLabel
from PyQt6.QtWidgets import QMenu
from PyQt6.QtWidgets import QPushButton
from PyQt6.QtWidgets import QToolButton
from PyQt6.QtWidgets import QWidget

_BUTTON_CONFIG = {
    "add": ("+ Add", 64, None),
    "remove": ("- Remove", 80, None),
    "enable": ("Enable All", 80, "Enable all rows"),
    "disable": ("Disable All", 82, "Disable all rows"),
    "file": ("Browse File...", 100, "Select a file to upload for the selected row"),
}

_PRESETS_BUTTON_TEXT = "Presets v"
_PRESETS_BUTTON_TOOLTIP = "Insert a common preset"
_LAYOUT_MARGINS = (0, 2, 0, 0)
_LAYOUT_SPACING = 2


class _SignalEmitter(Protocol):
    def emit(self) -> None: ...


class TabToolbar(QWidget):
    """Toolbar for managing table rows with optional presets and file selection."""

    add_clicked = pyqtSignal()
    remove_clicked = pyqtSignal()
    enable_all_clicked = pyqtSignal()
    disable_all_clicked = pyqtSignal()
    preset_selected = pyqtSignal(str, str)
    file_browse_clicked = pyqtSignal()

    def __init__(
        self,
        label: str | None = None,
        *,
        presets: Sequence[tuple[str, str, str] | None] | None = None,
        preset_context: str | None = None,
        include_file_btn: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._presets = presets or []
        self._preset_context = self._normalize_context(preset_context or "toolbar_presets")
        self._validate_presets(self._presets)

        layout = self._create_layout()
        self.setLayout(layout)

        if label:
            self._add_title_label(layout, label)

        self._add_standard_buttons(layout)
        layout.addStretch()

        if self._presets:
            self._add_presets_menu(layout)
        if include_file_btn:
            self._add_file_button(layout)

    @staticmethod
    def _validate_presets(presets: Sequence[tuple[str, str, str] | None] | None) -> None:
        """Validate preset structure for predictable menu rendering."""
        for index, preset in enumerate(presets or []):
            if preset is None:
                continue
            if (
                not isinstance(preset, tuple)
                or len(preset) != 3
                or not all(isinstance(value, str) for value in preset)
            ):
                raise ValueError(
                    f"Preset {index} must be None or a tuple of 3 strings (display, key, value)",
                )

    @staticmethod
    def _create_layout() -> QHBoxLayout:
        """Create the main toolbar layout."""
        layout = QHBoxLayout()
        layout.setContentsMargins(*_LAYOUT_MARGINS)
        layout.setSpacing(_LAYOUT_SPACING)
        return layout

    @staticmethod
    def _add_title_label(layout: QHBoxLayout, label: str) -> None:
        """Add an optional title label to the toolbar."""
        title = QLabel(label)
        title.setObjectName("title")
        layout.addWidget(title)

    def _add_standard_buttons(self, layout: QHBoxLayout) -> None:
        """Add the standard row-management buttons."""
        button_specs = [
            ("add", self.add_clicked),
            ("remove", self.remove_clicked),
            ("enable", self.enable_all_clicked),
            ("disable", self.disable_all_clicked),
        ]
        for key, signal in button_specs:
            self._add_button(layout, key, signal)

    def _add_button(self, layout: QHBoxLayout, key: str, signal: _SignalEmitter) -> None:
        """Create a configured toolbar button and connect it to a signal."""
        text, width, tooltip = _BUTTON_CONFIG[key]
        button = QPushButton(text)
        button.setMinimumWidth(width)
        if tooltip:
            button.setToolTip(tooltip)
        button.clicked.connect(signal.emit)
        layout.addWidget(button)

    def _add_presets_menu(self, layout: QHBoxLayout) -> None:
        """Create the optional presets menu button."""
        button = QToolButton()
        button.setText(_PRESETS_BUTTON_TEXT)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        button.setToolTip(_PRESETS_BUTTON_TOOLTIP)

        menu = QMenu(button)
        self._presets_menu = menu
        self._rebuild_presets_menu()
        menu.aboutToShow.connect(self._rebuild_presets_menu)

        button.setMenu(menu)
        layout.addWidget(button)

    @staticmethod
    def _normalize_context(value: str) -> str:
        """Normalize a presets usage-tracking context into a stable slug."""
        slug = re.sub(r"[^a-z0-9]+", "_", (value or "").strip().lower())
        return slug.strip("_") or "toolbar_presets"

    @staticmethod
    def _preset_usage_key(key: str, value: str) -> str:
        """Build a stable usage-tracking key for a preset entry."""
        raw = f"{key}:{value}".strip().lower()
        slug = re.sub(r"[^a-z0-9]+", "_", raw)
        return f"preset.{slug.strip('_') or 'unnamed'}"

    def _preset_usage_count(self, key: str, value: str) -> int:
        """Return how often a preset has been used in this context."""
        tracker = getattr(self.window(), "_ui_usage_tracker", None)
        if tracker is None:
            return 0
        try:
            return int(
                tracker.get_count(
                    category="preset",
                    context=self._preset_context,
                    element_id=self._preset_usage_key(key, value),
                ),
            )
        except Exception:
            return 0

    def _record_preset_usage(self, key: str, value: str) -> None:
        """Persist a preset usage hit when tracking is available."""
        tracker = getattr(self.window(), "_ui_usage_tracker", None)
        if tracker is None:
            return
        try:
            tracker.record(
                self._preset_usage_key(key, value),
                category="preset",
                context=self._preset_context,
            )
        except Exception:
            return

    def _on_preset_triggered(self, key: str, value: str) -> None:
        """Record preset usage and emit the selection signal."""
        self._record_preset_usage(key, value)
        self.preset_selected.emit(key, value)

    def _rebuild_presets_menu(self) -> None:
        """Sort presets by usage within each separator-defined group."""
        if not hasattr(self, "_presets_menu"):
            return
        menu = self._presets_menu
        menu.clear()

        groups: list[list[tuple[int, str, str, str]]] = [[]]
        for index, preset in enumerate(self._presets):
            if preset is None:
                if groups[-1]:
                    groups.append([])
                continue
            display, key, value = preset
            groups[-1].append((index, display, key, value))

        compact_groups = [group for group in groups if group]
        for group_index, group in enumerate(compact_groups):
            ranked: list[tuple[int, int, str, str, str]] = []
            for original_index, display, key, value in group:
                ranked.append(
                    (
                        -self._preset_usage_count(key, value),
                        original_index,
                        display,
                        key,
                        value,
                    ),
                )
            ranked.sort(key=lambda item: (item[0], item[1]))
            for _, _, display, key, value in ranked:
                menu.addAction(display, lambda k=key, v=value: self._on_preset_triggered(k, v))
            if group_index < len(compact_groups) - 1:
                menu.addSeparator()

    def _add_file_button(self, layout: QHBoxLayout) -> None:
        """Add the optional file-browse button."""
        self._add_button(layout, "file", self.file_browse_clicked)
