from PyQt6.QtWidgets import QTableWidget, QHeaderView, QTableWidgetItem


class HeaderTable(QTableWidget):
    """Read-only table for displaying headers with built-in filter."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setColumnCount(2)
        self.setHorizontalHeaderLabels(["Header", "Value"])
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.horizontalHeader().setDefaultSectionSize(200)
        self.verticalHeader().setVisible(False)
        self.setAlternatingRowColors(True)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._all_headers: dict = {}

    def load(self, headers: dict) -> None:
        """Load a headers dict into the table (keeps internal copy for filtering)."""
        self._all_headers = dict(sorted((k, v) for k, v in headers.items()))
        self._apply_filter("")

    def filter(self, text: str) -> None:
        """Filter visible rows by the provided text (case-insensitive)."""
        self._apply_filter(text)

    def _apply_filter(self, text: str) -> None:
        term = text.lower().strip()
        rows = [
            (k, v) for k, v in self._all_headers.items()
            if not term or term in k.lower() or term in str(v).lower()
        ]
        self.setRowCount(len(rows))
        for row, (k, v) in enumerate(rows):
            self.setItem(row, 0, QTableWidgetItem(k))
            self.setItem(row, 1, QTableWidgetItem(str(v)))
        self.resizeRowsToContents()
