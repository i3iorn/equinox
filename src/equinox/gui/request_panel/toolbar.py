"""Reusable toolbar widget for RequestPanel tabs.

Provides a consistent left-aligned label and standard control buttons
(Add, Remove, Enable All, Disable All) and optional presets menu or
file-browse button. Emits PyQt signals which the parent `RequestPanel`
can connect to so table updates are handled centrally.
"""
import logging
from typing import Iterable, Optional, Tuple

from PyQt6.QtWidgets import (
    QWidget,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QToolButton,
    QMenu,
)
from PyQt6.QtCore import pyqtSignal

logger = logging.getLogger(__name__)


class TabToolbar(QWidget):
    add_clicked = pyqtSignal()
    remove_clicked = pyqtSignal()
    enable_all_clicked = pyqtSignal()
    disable_all_clicked = pyqtSignal()
    preset_selected = pyqtSignal(str, str)  # key, value
    file_browse_clicked = pyqtSignal()

    def __init__(self, label: Optional[str] = None, *, presets: Optional[Iterable[Optional[Tuple[str, str, str]]]] = None, include_file_btn: bool = False, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 0)
        layout.setSpacing(2)

        if label:
            lbl = QLabel(label)
            lbl.setStyleSheet("font-weight: bold;")
            layout.addWidget(lbl)

        # Add/Remove
        self._add_btn = QPushButton("+ Add")
        self._add_btn.setFixedWidth(64)
        self._add_btn.clicked.connect(lambda: self.add_clicked.emit())
        layout.addWidget(self._add_btn)

        self._remove_btn = QPushButton("− Remove")
        self._remove_btn.setFixedWidth(80)
        self._remove_btn.clicked.connect(lambda: self.remove_clicked.emit())
        layout.addWidget(self._remove_btn)

        # Enable/Disable
        self._enable_btn = QPushButton("Enable All")
        self._enable_btn.setFixedWidth(80)
        self._enable_btn.setToolTip("Enable all rows")
        self._enable_btn.clicked.connect(lambda: self.enable_all_clicked.emit())
        layout.addWidget(self._enable_btn)

        self._disable_btn = QPushButton("Disable All")
        self._disable_btn.setFixedWidth(82)
        self._disable_btn.setToolTip("Disable all rows")
        self._disable_btn.clicked.connect(lambda: self.disable_all_clicked.emit())
        layout.addWidget(self._disable_btn)

        layout.addStretch()

        # Optional presets menu
        if presets:
            presets_btn = QToolButton()
            presets_btn.setText("Presets ▾")
            presets_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            presets_btn.setToolTip("Insert a common header")
            presets_menu = QMenu(presets_btn)
            for preset in presets:
                if preset is None:
                    presets_menu.addSeparator()
                else:
                    lbl, key, value = preset
                    act = presets_menu.addAction(lbl)
                    # use lambda capturing to forward key/value
                    act.triggered.connect(lambda checked=False, k=key, v=value: self.preset_selected.emit(k, v))
            presets_btn.setMenu(presets_menu)
            layout.addWidget(presets_btn)

        # Optional file browse button (used by multipart)
        if include_file_btn:
            self._file_btn = QPushButton("Browse File…")
            self._file_btn.setFixedWidth(100)
            self._file_btn.setToolTip("Select a file to upload for the selected row")
            self._file_btn.clicked.connect(lambda: self.file_browse_clicked.emit())
            layout.addWidget(self._file_btn)

