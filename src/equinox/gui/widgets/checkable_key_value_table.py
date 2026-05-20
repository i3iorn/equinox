"""Key-value table with per-row enable/disable checkboxes (for Params)."""

from __future__ import annotations

import logging

from PyQt6.QtCore import QStringListModel, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCompleter,
    QHeaderView,
    QLineEdit,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
)

logger = logging.getLogger(__name__)

# Common HTTP request headers for auto-complete
_COMMON_HTTP_HEADERS = [
    "Accept",
    "Accept-Charset",
    "Accept-Encoding",
    "Accept-Language",
    "Authorization",
    "Cache-Control",
    "Connection",
    "Content-Disposition",
    "Content-Encoding",
    "Content-Language",
    "Content-Length",
    "Content-Type",
    "Cookie",
    "Date",
    "Expect",
    "From",
    "Host",
    "If-Match",
    "If-Modified-Since",
    "If-None-Match",
    "If-Range",
    "If-Unmodified-Since",
    "Max-Forwards",
    "Origin",
    "Pragma",
    "Proxy-Authorization",
    "Range",
    "Referer",
    "TE",
    "Transfer-Encoding",
    "Upgrade",
    "User-Agent",
    "Via",
    "Warning",
    "X-API-Key",
    "X-Auth-Token",
    "X-Correlation-ID",
    "X-Forwarded-For",
    "X-Forwarded-Host",
    "X-Forwarded-Proto",
    "X-Real-IP",
    "X-Request-ID",
]


class _HeaderCompleterDelegate(QStyledItemDelegate):
    """QStyledItemDelegate that attaches a QCompleter to Key-column editors."""

    def __init__(self, parent=None):
        super().__init__(parent)
        # Build the model once so every cell editor shares the same data
        # instead of allocating a new QStringListModel on every keypress.
        self._model = QStringListModel(_COMMON_HTTP_HEADERS, self)

    def createEditor(self, parent, option, index):
        editor = super().createEditor(parent, option, index)
        if isinstance(editor, QLineEdit):
            completer = QCompleter(self._model, editor)
            completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            completer.setFilterMode(Qt.MatchFlag.MatchContains)
            completer.setMaxVisibleItems(10)
            editor.setCompleter(completer)
        return editor


class CheckableKeyValueTable(QTableWidget):
    """Key-value table where each row has an enable/disable checkbox.

    Column layout: [✓ | Key | Value]

    ``get_enabled_data()`` → dict only for checked rows (used when sending).
    ``get_all_rows()``     → list[dict] with all non-empty rows + enabled flag
                             (used when saving / displaying the badge count).
    ``set_data()``         → accepts either dict[str, str] (all enabled) or
                             list[dict] with optional ``"enabled"`` key.

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

    # Column indices — used throughout to avoid magic numbers.
    _COL_ENABLED: int = 0
    _COL_KEY: int = 1
    _COL_VALUE: int = 2

    def __init__(self, parent=None, *, enable_key_completer: bool = False):
        super().__init__(parent)
        self._updating = False  # reentrancy guard for _on_item_changed
        self.setColumnCount(3)
        self.setHorizontalHeaderLabels(["", "Key", "Value"])
        header = self.horizontalHeader()
        header.setSectionResizeMode(self._COL_ENABLED, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(self._COL_KEY, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(self._COL_VALUE, QHeaderView.ResizeMode.Stretch)
        self.setColumnWidth(self._COL_ENABLED, 26)
        self.verticalHeader().setVisible(False)
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        if enable_key_completer:
            self._header_delegate = _HeaderCompleterDelegate(self)
            self.setItemDelegateForColumn(self._COL_KEY, self._header_delegate)
        self._add_empty_row(enabled=False)
        self.itemChanged.connect(self._on_item_changed)

    # ── Private helpers ───────────────────────────────────────────────

    @staticmethod
    def _make_checkbox(enabled: bool = False) -> QTableWidgetItem:
        """Create a checkbox-only item for the enabled column."""
        item = QTableWidgetItem()
        item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
        item.setCheckState(Qt.CheckState.Checked if enabled else Qt.CheckState.Unchecked)
        return item

    def _set_row_items(self, row: int, key: str, value: str, enabled: bool) -> None:
        """Populate the three cells of an already-inserted *row*."""
        self.setItem(row, self._COL_ENABLED, self._make_checkbox(enabled))
        self.setItem(row, self._COL_KEY, QTableWidgetItem(key))
        self.setItem(row, self._COL_VALUE, QTableWidgetItem(value))

    def _iter_non_empty_rows(self):
        """Yield ``(key, value, enabled)`` for every row with a non-empty key.

        Skips the trailing empty sentinel row transparently so callers never
        need to guard against blank entries.
        """
        for row in range(self.rowCount()):
            key_item = self.item(row, self._COL_KEY)
            if not (key_item and key_item.text().strip()):
                continue
            checkbox = self.item(row, self._COL_ENABLED)
            value_item = self.item(row, self._COL_VALUE)
            enabled = bool(checkbox and checkbox.checkState() == Qt.CheckState.Checked)
            yield key_item.text().strip(), (value_item.text() if value_item else ""), enabled

    def _add_empty_row(self, enabled: bool = False) -> None:
        """Append a trailing empty sentinel row.

        Saves and restores the pre-existing blocked-signals state so this
        helper is safe to call from inside an already-blocked context.
        """
        row = self.rowCount()
        was_blocked = self.signalsBlocked()
        self.blockSignals(True)
        try:
            self.insertRow(row)
            self._set_row_items(row, "", "", enabled)
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
        # extra click.
        if col == self._COL_KEY and item.text().strip():
            checkbox = self.item(row, self._COL_ENABLED)
            if checkbox and checkbox.checkState() == Qt.CheckState.Unchecked:
                self._updating = True
                try:
                    checkbox.setCheckState(Qt.CheckState.Checked)
                finally:
                    self._updating = False

        # Auto-add a fresh trailing empty row when the user starts filling in
        # the last row, so there is always somewhere to add the next entry.
        if (
            col in (self._COL_KEY, self._COL_VALUE)
            and row == self.rowCount() - 1
            and item.text().strip()
        ):
            self._add_empty_row(enabled=False)

        self.data_changed.emit()

    # ── Data accessors ────────────────────────────────────────────────

    def get_enabled_data(self) -> dict[str, str]:
        """Return only checked, non-empty-key rows as a plain dict."""
        data: dict[str, str] = {}
        for key, value, enabled in self._iter_non_empty_rows():
            if enabled:
                if key in data:
                    logger.debug("Duplicate key %r; last value wins", key)
                data[key] = value
        return data

    def get_all_rows(self) -> list[dict]:
        """Return all non-empty-key rows with their enabled flag."""
        return [
            {"key": key, "value": value, "enabled": enabled}
            for key, value, enabled in self._iter_non_empty_rows()
        ]

    def get_data(self) -> dict[str, str]:
        """Backward-compat alias for get_enabled_data."""
        return self.get_enabled_data()

    def set_data(self, data) -> None:
        """Load rows.

        ``data`` can be:
        - ``dict[str, str]`` — all rows will be enabled.
        - ``list[dict]``     — each dict has ``key``, ``value``, ``enabled``.
        """
        was_blocked = self.signalsBlocked()
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
                self._set_row_items(
                    row,
                    str(row_data.get("key", "")),
                    str(row_data.get("value", "")),
                    bool(row_data.get("enabled", False)),
                )
            self._add_empty_row(enabled=False)
        finally:
            self.blockSignals(was_blocked)

    def add_row(self, key: str = "", value: str = "", enabled: bool = True) -> None:
        """Insert a new row before the trailing empty row."""
        was_blocked = self.signalsBlocked()
        self.blockSignals(True)
        try:
            # Remove the trailing empty sentinel so the new row precedes it.
            last = self.rowCount() - 1
            if last >= 0:
                last_key = self.item(last, self._COL_KEY)
                if last_key is None or not last_key.text().strip():
                    self.removeRow(last)
            row = self.rowCount()
            self.insertRow(row)
            self._set_row_items(row, key, value, enabled)
            self._add_empty_row(enabled=False)
        finally:
            self.blockSignals(was_blocked)

    def reset(self) -> None:
        """Clear all rows and add a fresh empty sentinel row."""
        was_blocked = self.signalsBlocked()
        self.blockSignals(True)
        try:
            self.setRowCount(0)
            self._add_empty_row(enabled=False)
        finally:
            self.blockSignals(was_blocked)
