"""Auto-growing key-value table widget."""

from typing import Dict

from PyQt6.QtWidgets import QTableWidget, QTableWidgetItem, QHeaderView
from PyQt6.QtCore import pyqtSignal


class KeyValueTable(QTableWidget):
    """QTableWidget for key-value pairs that adds an empty row automatically
    when the user starts typing in the last row.

    Signals
    -------
    data_changed()
        Emitted after any user-driven change to a key or value cell.
        Not emitted during bulk operations (``set_data``, ``reset``).
    """

    data_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setColumnCount(2)
        self.setHorizontalHeaderLabels(["Key", "Value"])
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.verticalHeader().setVisible(False)
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._add_empty_row()
        self.itemChanged.connect(self._on_item_changed)

    def _add_empty_row(self) -> None:
        """Append a trailing empty sentinel row.

        Internally blocks signals (save/restore) so the two ``setItem`` calls
        do not trigger ``_on_item_changed`` or ``data_changed``.  Safe whether
        or not the caller has already blocked signals.
        """
        row = self.rowCount()
        was_blocked = self.signalsBlocked()
        self.blockSignals(True)
        try:
            self.insertRow(row)
            self.setItem(row, 0, QTableWidgetItem(""))
            self.setItem(row, 1, QTableWidgetItem(""))
        finally:
            self.blockSignals(was_blocked)

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item.row() == self.rowCount() - 1 and item.text().strip():
            self._add_empty_row()
        self.data_changed.emit()

    def get_data(self) -> Dict[str, str]:
        data: Dict[str, str] = {}
        for row in range(self.rowCount()):
            key_item = self.item(row, 0)
            value_item = self.item(row, 1)
            if key_item and value_item and key_item.text().strip():
                # Strip keys (whitespace there is never meaningful) but preserve
                # value whitespace — a value like " Bearer token" is intentional.
                data[key_item.text().strip()] = value_item.text()
        return data

    def set_data(self, data: Dict[str, str]) -> None:
        self.blockSignals(True)
        try:
            self.setRowCount(0)
            for key, value in data.items():
                row = self.rowCount()
                self.insertRow(row)
                self.setItem(row, 0, QTableWidgetItem(str(key)))
                self.setItem(row, 1, QTableWidgetItem(str(value)))
            self._add_empty_row()
        finally:
            self.blockSignals(False)

    def reset(self) -> None:
        self.blockSignals(True)
        try:
            self.setRowCount(0)
            self._add_empty_row()
        finally:
            self.blockSignals(False)
