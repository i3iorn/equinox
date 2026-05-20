"""Reusable toolbar widget for RequestPanel tabs.

Provides a consistent left-aligned label and standard control buttons
(Add, Remove, Enable All, Disable All) and optional presets menu or
file-browse button. Emits PyQt signals which the parent `RequestPanel`
can connect to so table updates are handled centrally.
"""

import re
from collections.abc import Sequence
from typing import Optional, Protocol, Tuple

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QToolButton,
    QWidget,
)

# Button configuration: (text, width, tooltip)
_BUTTON_CONFIG = {
    "add": ("+ Add", 64, None),
    "remove": ("- Remove", 80, None),
    "enable": ("Enable All", 80, "Enable all rows"),
    "disable": ("Disable All", 82, "Disable all rows"),
    "file": ("Browse File...", 100, "Select a file to upload for the selected row"),
}

_PRESETS_BUTTON_TEXT = "Presets v"
_PRESETS_BUTTON_TOOLTIP = "Insert a common header"
_LAYOUT_MARGINS = (0, 2, 0, 0)
_LAYOUT_SPACING = 2


class _SignalEmitter(Protocol):
    def emit(self) -> None: ...


class TabToolbar(QWidget):
    """Toolbar for managing rows in a table with optional presets and file selection."""

    add_clicked = pyqtSignal()
    remove_clicked = pyqtSignal()
    enable_all_clicked = pyqtSignal()
    disable_all_clicked = pyqtSignal()
    preset_selected = pyqtSignal(str, str)  # (key, value)
    file_browse_clicked = pyqtSignal()

    def __init__(
        self,
        label: Optional[str] = None,
        *,
        presets: Optional[Sequence[Optional[Tuple[str, str, str]]]] = None,
        preset_context: Optional[str] = None,
        include_file_btn: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        """Initialize the toolbar.

        Args:
            label: Optional title label for the toolbar.
            presets: Optional sequence of preset tuples (display, key, value) or None for separators.
            include_file_btn: If True, add a file browse button.
            parent: Parent widget.

        Raises:
            ValueError: If a preset tuple has incorrect structure.
        """
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
    def _validate_presets(presets: Optional[Sequence[Optional[Tuple[str, str, str]]]]) -> None:
        """Validate preset structure for type safety.

        Args:
            presets: Sequence to validate.

        Raises:
            ValueError: If any non-None preset doesn't have exactly 3 string elements.
        """
        for i, preset in enumerate(presets or []):
            if preset is None:
                continue
            if (
                not isinstance(preset, tuple)
                or len(preset) != 3
                or not all(isinstance(s, str) for s in preset)
            ):
                raise ValueError(
                    f"Preset {i} must be None or a tuple of 3 strings (display, key, value)"
                )

    @staticmethod
    def _create_layout() -> QHBoxLayout:
        """Create and configure the main layout."""
        layout = QHBoxLayout()
        layout.setContentsMargins(*_LAYOUT_MARGINS)
        layout.setSpacing(_LAYOUT_SPACING)
        return layout

    @staticmethod
    def _add_title_label(layout: QHBoxLayout, label: str) -> None:
        """Add a title label to the layout.

        Args:
            layout: The layout to add to.
            label: The label text.
        """
        title = QLabel(label)
        title.setObjectName("title")
        layout.addWidget(title)

    def _add_standard_buttons(self, layout: QHBoxLayout) -> None:
        """Add standard Add/Remove/Enable/Disable buttons.

        Args:
            layout: The layout to add buttons to.
        """
        button_specs = [
            ("add", self.add_clicked),
            ("remove", self.remove_clicked),
            ("enable", self.enable_all_clicked),
            ("disable", self.disable_all_clicked),
        ]

        for key, signal in button_specs:
            self._add_button(layout, key, signal)

    def _add_button(self, layout: QHBoxLayout, key: str, signal: _SignalEmitter) -> None:
        """Create and add a configured button to the layout.

        Args:
            layout: The layout to add to.
            key: Key into _BUTTON_CONFIG.
            signal: Signal to emit on click.
        """
        text, width, tooltip = _BUTTON_CONFIG[key]
        btn = QPushButton(text)
        # Keep compact defaults while allowing wider text/font settings to fit.
        btn.setMinimumWidth(width)
        if tooltip:
            btn.setToolTip(tooltip)
        btn.clicked.connect(signal.emit)
        layout.addWidget(btn)

    def _add_presets_menu(self, layout: QHBoxLayout) -> None:
        """Create and add a presets menu button.

        Args:
            layout: The layout to add to.
        """
        btn = QToolButton()
        btn.setText(_PRESETS_BUTTON_TEXT)
        btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        btn.setToolTip(_PRESETS_BUTTON_TOOLTIP)

        menu = QMenu(btn)
        self._presets_menu = menu
        self._rebuild_presets_menu()
        menu.aboutToShow.connect(self._rebuild_presets_menu)

        btn.setMenu(menu)
        layout.addWidget(btn)

    @staticmethod
    def _normalize_context(value: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "_", (value or "").strip().lower())
        return slug.strip("_") or "toolbar_presets"

    @staticmethod
    def _preset_usage_key(key: str, value: str) -> str:
        raw = f"{key}:{value}".strip().lower()
        slug = re.sub(r"[^a-z0-9]+", "_", raw)
        return f"preset.{slug.strip('_') or 'unnamed'}"

    def _preset_usage_count(self, key: str, value: str) -> int:
        tracker = getattr(self.window(), "_ui_usage_tracker", None)
        if tracker is None:
            return 0
        try:
            count = tracker.get_count(
                category="preset",
                context=self._preset_context,
                element_id=self._preset_usage_key(key, value),
            )
            return int(count)
        except Exception:
            return 0

    def _record_preset_usage(self, key: str, value: str) -> None:
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
            pass

    def _on_preset_triggered(self, key: str, value: str) -> None:
        self._record_preset_usage(key, value)
        self.preset_selected.emit(key, value)

    def _rebuild_presets_menu(self) -> None:
        """Sort presets by usage within each separator group for predictability."""
        if not hasattr(self, "_presets_menu"):
            return
        menu = self._presets_menu
        menu.clear()

        groups: list[list[tuple[int, str, str, str]]] = [[]]
        for idx, preset in enumerate(self._presets):
            if preset is None:
                if groups[-1]:
                    groups.append([])
                continue
            display, key, value = preset
            groups[-1].append((idx, display, key, value))

        groups = [g for g in groups if g]
        for group_idx, group in enumerate(groups):
            ranked: list[tuple[int, int, str, str, str]] = []
            for original_idx, display, key, value in group:
                ranked.append(
                    (
                        -self._preset_usage_count(key, value),
                        original_idx,
                        display,
                        key,
                        value,
                    )
                )
            ranked.sort(key=lambda item: (item[0], item[1]))
            for _, _, display, key, value in ranked:
                menu.addAction(
                    display,
                    lambda k=key, v=value: self._on_preset_triggered(k, v),
                )
            if group_idx < len(groups) - 1:
                menu.addSeparator()

    def _add_file_button(self, layout: QHBoxLayout) -> None:
        """Create and add a file browse button.

        Args:
            layout: The layout to add to.
        """
        self._add_button(layout, "file", self.file_browse_clicked)
