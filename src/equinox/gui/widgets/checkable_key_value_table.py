"""Key-value table with per-row enable/disable checkboxes (for Params)."""

import logging
from typing import Dict

from PyQt6.QtWidgets import (
    QTableWidget, QTableWidgetItem, QHeaderView, QStyledItemDelegate, QLineEdit,
)
from PyQt6.QtCore import Qt, pyqtSignal

logger = logging.getLogger(__name__)

# Common HTTP request headers for auto-complete
_COMMON_HTTP_HEADERS = [
    "Accept", "Accept-Charset", "Accept-Encoding", "Accept-Language",
    "Authorization", "Cache-Control", "Connection", "Content-Disposition",
    "Content-Encoding", "Content-Language", "Content-Length", "Content-Type",
    "Cookie", "Date", "Expect", "From", "Host", "If-Match",
    "If-Modified-Since", "If-None-Match", "If-Range", "If-Unmodified-Since",
    "Max-Forwards", "Origin", "Pragma", "Proxy-Authorization",
    "Range", "Referer", "TE", "Transfer-Encoding", "Upgrade",
    "User-Agent", "Via", "Warning",
    "X-API-Key", "X-Auth-Token", "X-Correlation-ID",
    "X-Forwarded-For", "X-Forwarded-Host", "X-Forwarded-Proto",
    "X-Real-IP", "X-Request-ID",
]


class _HeaderCompleterDelegate(QStyledItemDelegate):
    """QStyledItemDelegate that attaches a QCompleter to Key-column editors."""

    def __init__(self, parent=None):
        super().__init__(parent)
        # Build the model once so every cell editor shares the same data
        # instead of allocating a new QStringListModel on every keypress.
        from PyQt6.QtCore import QStringListModel
        self._model = QStringListModel(_COMMON_HTTP_HEADERS, self)

    def createEditor(self, parent, option, index):
        editor = super().createEditor(parent, option, index)
        if isinstance(editor, QLineEdit):
            from PyQt6.QtWidgets import QCompleter
            completer = QCompleter(self._model, editor)
            completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            completer.setFilterMode(Qt.MatchFlag.MatchContains)
            completer.setMaxVisibleItems(10)
            editor.setCompleter(completer)
        return editor


class CheckableKeyValueTable(QTableWidget):
    """Key-value table where each row has an enable/disable checkbox.

    Column layout: [✓ | Key | Value]

    ``get_enabled_data()`` → Dict only for checked rows (used when sending).
    ``get_all_rows()``     → List[Dict] with all non-empty rows + enabled flag
                             (used when saving / displaying the badge count).
    ``set_data()``         → accepts either Dict[str, str] (all enabled) or
                             List[Dict] with optional ``"enabled"`` key.

    Signals
    -------
    data_changed()
        Emitted after any user-driven change to a key, value, or checkbox state.
        Not emitted during bulk operations (``set_data``, ``add_row``, ``reset``).

    Parameters
    ----------
    enable_key_completer : bool
        When *True*, install a completer on the Key column that suggests
        common HTTP header names.  Leave *False* for parameter tables where
        header suggestions would be confusing.
    """

    data_changed = pyqtSignal()

    def __init__(self, parent=None, *, enable_key_completer: bool = False):
        super().__init__(parent)
        self._updating = False  # reentrancy guard for _on_item_changed
        self.setColumnCount(3)
        self.setHorizontalHeaderLabels(["", "Key", "Value"])
        header = self.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.setColumnWidth(0, 26)
        self.verticalHeader().setVisible(False)
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        if enable_key_completer:
            self._header_delegate = _HeaderCompleterDelegate(self)
            self.setItemDelegateForColumn(1, self._header_delegate)
        self._add_empty_row(enabled=False)
        self.itemChanged.connect(self._on_item_changed)

    # ── Helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _make_checkbox(enabled: bool = False) -> QTableWidgetItem:
        """Create a checkbox-only item for column 0."""
        item = QTableWidgetItem()
        item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
        item.setCheckState(Qt.CheckState.Checked if enabled else Qt.CheckState.Unchecked)
        return item

    def _add_empty_row(self, enabled: bool = False) -> None:
        """Append a trailing empty sentinel row.

        Internally blocks signals (save/restore) so that the three ``setItem``
        calls do not trigger ``_on_item_changed`` or ``data_changed``.  This is
        safe whether or not the caller has already blocked signals.
        """
        row = self.rowCount()
        was_blocked = self.signalsBlocked()
        self.blockSignals(True)
        try:
            self.insertRow(row)
            self.setItem(row, 0, self._make_checkbox(enabled))
            self.setItem(row, 1, QTableWidgetItem(""))
            self.setItem(row, 2, QTableWidgetItem(""))
        finally:
            self.blockSignals(was_blocked)

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        # Reentrancy guard: changes made programmatically inside this handler
        # (e.g. auto-checking the checkbox) must not trigger a second pass.
        if self._updating:
            return

        row = item.row()
        col = item.column()

        # Auto-enable the row's checkbox as soon as the user types a key so
        # the row is immediately visible to get_enabled_data() without an
        # extra click.  The guard prevents the resulting itemChanged for the
        # checkbox from re-entering this handler.
        if col == 1 and item.text().strip():
            checkbox = self.item(row, 0)
            if checkbox and checkbox.checkState() == Qt.CheckState.Unchecked:
                self._updating = True
                try:
                    checkbox.setCheckState(Qt.CheckState.Checked)
                finally:
                    self._updating = False

        # Auto-add a fresh trailing empty row when the user starts filling in
        # the last row, so there is always somewhere to add the next entry.
        if col in (1, 2) and row == self.rowCount() - 1 and item.text().strip():
            self._add_empty_row(enabled=False)

        self.data_changed.emit()

    # ── Data accessors ────────────────────────────────────────────────

    def get_enabled_data(self) -> Dict[str, str]:
        """Return only checked, non-empty-key rows as a plain dict."""
        data: Dict[str, str] = {}
        for row in range(self.rowCount()):
            checkbox = self.item(row, 0)
            key_item = self.item(row, 1)
            value_item = self.item(row, 2)
            if key_item and key_item.text().strip():
                if checkbox and checkbox.checkState() == Qt.CheckState.Checked:
                    key = key_item.text().strip()
                    if key in data:
                        logger.debug(
                            "Duplicate key %r at row %d; last value wins", key, row
                        )
                    data[key] = value_item.text() if value_item else ""
        return data

    def get_all_rows(self) -> list:
        """Return all non-empty-key rows with their enabled flag."""
        rows = []
        for row in range(self.rowCount()):
            checkbox = self.item(row, 0)
            key_item = self.item(row, 1)
            value_item = self.item(row, 2)
            if key_item and key_item.text().strip():
                enabled = bool(checkbox and checkbox.checkState() == Qt.CheckState.Checked)
                rows.append({
                    "key": key_item.text().strip(),
                    "value": value_item.text() if value_item else "",
                    "enabled": enabled,
                })
        return rows

    def get_data(self) -> Dict[str, str]:
        """Backward-compat alias for get_enabled_data."""
        return self.get_enabled_data()

    def set_data(self, data) -> None:
        """Load rows.

        ``data`` can be:
        - ``Dict[str, str]`` — all rows will be enabled.
        - ``List[Dict]``     — each dict has ``key``, ``value``, ``enabled``.
        """
        self.blockSignals(True)
        try:
            self.setRowCount(0)
            if isinstance(data, dict):
                rows = [{"key": k, "value": v, "enabled": True} for k, v in data.items()]
            else:
                rows = list(data)
            for row_data in rows:
                row = self.rowCount()
                self.insertRow(row)
                enabled = row_data.get("enabled", False)
                self.setItem(row, 0, self._make_checkbox(enabled))
                self.setItem(row, 1, QTableWidgetItem(str(row_data.get("key", ""))))
                self.setItem(row, 2, QTableWidgetItem(str(row_data.get("value", ""))))
            self._add_empty_row(enabled=False)
        finally:
            self.blockSignals(False)

    def add_row(self, key: str = "", value: str = "", enabled: bool = True) -> None:
        """Insert a new row before the trailing empty row."""
        self.blockSignals(True)
        try:
            last = self.rowCount() - 1
            last_key = self.item(last, 1) if last >= 0 else None
            if last >= 0 and (last_key is None or not last_key.text().strip()):
                self.removeRow(last)
            row = self.rowCount()
            self.insertRow(row)
            self.setItem(row, 0, self._make_checkbox(enabled))
            self.setItem(row, 1, QTableWidgetItem(key))
            self.setItem(row, 2, QTableWidgetItem(value))
            self._add_empty_row(enabled=False)
        finally:
            self.blockSignals(False)

    def reset(self) -> None:
        self.blockSignals(True)
        try:
            self.setRowCount(0)
            self._add_empty_row(enabled=False)
        finally:
            self.blockSignals(False)
