"""Auto-growing key-value table widget."""

from typing import Dict

from PyQt6.QtWidgets import QTableWidget, QTableWidgetItem, QHeaderView


class KeyValueTable(QTableWidget):
    """QTableWidget for key-value pairs that adds an empty row automatically
    when the user starts typing in the last row."""

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
        row = self.rowCount()
        self.insertRow(row)
        self.setItem(row, 0, QTableWidgetItem(""))
        self.setItem(row, 1, QTableWidgetItem(""))

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item.row() == self.rowCount() - 1 and item.text().strip():
            self._add_empty_row()

    def get_data(self) -> Dict[str, str]:
        data: Dict[str, str] = {}
        for row in range(self.rowCount()):
            key_item = self.item(row, 0)
            value_item = self.item(row, 1)
            if key_item and value_item and key_item.text().strip():
                data[key_item.text().strip()] = value_item.text().strip()
        return data

    def set_data(self, data: Dict[str, str]) -> None:
        self.blockSignals(True)
        self.setRowCount(0)
        for key, value in data.items():
            row = self.rowCount()
            self.insertRow(row)
            self.setItem(row, 0, QTableWidgetItem(str(key)))
            self.setItem(row, 1, QTableWidgetItem(str(value)))
        self._add_empty_row()
        self.blockSignals(False)

    def reset(self) -> None:
        self.blockSignals(True)
        self.setRowCount(0)
        self.blockSignals(False)
        self._add_empty_row()

