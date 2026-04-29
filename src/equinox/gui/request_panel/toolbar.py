"""Reusable toolbar widget for RequestPanel tabs.

Provides a consistent left-aligned label and standard control buttons
(Add, Remove, Enable All, Disable All) and optional presets menu or
file-browse button. Emits PyQt signals which the parent `RequestPanel`
can connect to so table updates are handled centrally.
"""
from typing import Iterable, Optional, Sequence

from PyQt6.QtWidgets import (
    QWidget,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QToolButton,
    QMenu,
)
from PyQt6.QtCore import pyqtSignal


# Button configuration: (text, width, tooltip)
_BUTTON_CONFIG = {
    "add": ("+ Add", 64, None),
    "remove": ("− Remove", 80, None),
    "enable": ("Enable All", 80, "Enable all rows"),
    "disable": ("Disable All", 82, "Disable all rows"),
    "file": ("Browse File…", 100, "Select a file to upload for the selected row"),
}

_PRESETS_BUTTON_TEXT = "Presets ▾"
_PRESETS_BUTTON_TOOLTIP = "Insert a common header"
_LAYOUT_MARGINS = (0, 2, 0, 0)
_LAYOUT_SPACING = 2


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
        presets: Optional[Sequence[Optional[tuple[str, str, str]]]] = None,
        include_file_btn: bool = False,
        parent=None,
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
    def _validate_presets(
        presets: Sequence[Optional[tuple[str, str, str]]]
    ) -> None:
        """Validate preset structure for type safety.

        Args:
            presets: Sequence to validate.

        Raises:
            ValueError: If any non-None preset doesn't have exactly 3 string elements.
        """
        for i, preset in enumerate(presets):
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

    def _add_button(
        self, layout: QHBoxLayout, key: str, signal: pyqtSignal
    ) -> None:
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
        for preset in self._presets:
            if preset is None:
                menu.addSeparator()
            else:
                display, key, value = preset
                action = menu.addAction(display)
                # Capture values in lambda to avoid late binding issues
                action.triggered.connect(
                    lambda checked=False, k=key, v=value: self.preset_selected.emit(k, v)
                )

        btn.setMenu(menu)
        layout.addWidget(btn)

    def _add_file_button(self, layout: QHBoxLayout) -> None:
        """Create and add a file browse button.

        Args:
            layout: The layout to add to.
        """
        self._add_button(layout, "file", self.file_browse_clicked)
