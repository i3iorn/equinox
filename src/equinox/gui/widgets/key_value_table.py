"""Auto-growing key-value table widget."""

from collections.abc import Iterator
from contextlib import contextmanager

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtCore import QObject
from PyQt6.QtWidgets import QHeaderView
from PyQt6.QtWidgets import QTableWidget
from PyQt6.QtWidgets import QTableWidgetItem
from PyQt6.QtWidgets import QWidget

# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


@contextmanager
def _blocked(obj: QObject) -> Iterator[None]:
    """Block Qt signals on *obj* for the duration of the ``with`` block.

    Saves and restores the previous blocked state so this helper is safe to
    call from inside an already-blocked context without accidentally re-enabling
    signals on exit.
    """
    was_blocked = obj.signalsBlocked()
    obj.blockSignals(True)
    try:
        yield
    finally:
        obj.blockSignals(was_blocked)


# ---------------------------------------------------------------------------
# Widget
# ---------------------------------------------------------------------------


class KeyValueTable(QTableWidget):
    """QTableWidget for key-value pairs that adds an empty row automatically
    when the user starts typing in the last row.

    Signals
    -------
    data_changed()
        Emitted after any user-driven change to a key or value cell.
        Not emitted during bulk operations (``set_data``, ``reset``).
    """

    # Column indices — centralised so subclasses and callers never use magic numbers.
    _COL_KEY: int = 0
    _COL_VALUE: int = 1
    _COL_COUNT: int = 2

    data_changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setColumnCount(self._COL_COUNT)
        self.setHorizontalHeaderLabels(["Key", "Value"])
        header = self.horizontalHeader()
        if header is not None:
            header.setSectionResizeMode(self._COL_KEY, QHeaderView.ResizeMode.Interactive)
            header.setSectionResizeMode(self._COL_VALUE, QHeaderView.ResizeMode.Stretch)
        v_header = self.verticalHeader()
        if v_header is not None:
            v_header.setVisible(False)
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._add_empty_row()
        self.itemChanged.connect(self._on_item_changed)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _append_row(self, key: str, value: str) -> None:
        """Insert a single row at the bottom of the table.

        Does **not** manage signals — must be called from within a
        ``_blocked(self)`` context.
        """
        row = self.rowCount()
        self.insertRow(row)
        self.setItem(row, self._COL_KEY, QTableWidgetItem(key))
        self.setItem(row, self._COL_VALUE, QTableWidgetItem(value))

    def _add_empty_row(self) -> None:
        """Append the trailing empty sentinel row (signals are blocked internally)."""
        with _blocked(self):
            self._append_row("", "")

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item.row() == self.rowCount() - 1 and item.text().strip():
            self._add_empty_row()
        self.data_changed.emit()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_data(self) -> dict[str, str]:
        """Return all non-empty key-value pairs as a plain dictionary.

        Keys are stripped of surrounding whitespace (which is never meaningful
        for HTTP header names or query parameters).  Value whitespace is
        preserved — a value like ``" Bearer token"`` is intentional.
        """
        data: dict[str, str] = {}
        for row in range(self.rowCount()):
            key_item = self.item(row, self._COL_KEY)
            value_item = self.item(row, self._COL_VALUE)
            if key_item and value_item:
                key = key_item.text().strip()
                if key:
                    data[key] = value_item.text()
        return data

    def set_data(self, data: dict[str, str]) -> None:
        """Replace the table contents with *data*, then append an empty sentinel row.

        Signals are blocked for the entire operation; ``data_changed`` is **not**
        emitted.
        """
        with _blocked(self):
            self.setRowCount(0)
            for key, value in data.items():
                self._append_row(str(key), str(value))
            self._append_row("", "")

    def reset(self) -> None:
        """Clear all rows and restore a single empty sentinel row.

        Equivalent to ``set_data({})``.  ``data_changed`` is **not** emitted.
        """
        self.set_data({})
