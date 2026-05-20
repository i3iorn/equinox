"""Read-only header table widget with filtering.

Provides a QTableWidget subclass for displaying HTTP headers with:
- Efficient filtering (case-insensitive substring matching)
- Automatic sorting (headers are alphabetically sorted by name)
- Batched UI updates to prevent flickering during filter operations
- Read-only, non-editable cells with row selection
"""

from __future__ import annotations

import logging

from PyQt6.QtWidgets import QHeaderView, QTableWidget, QTableWidgetItem, QWidget

logger = logging.getLogger(__name__)

# Table configuration constants
_COLUMN_COUNT = 2
_NAME_COLUMN = 0
_VALUE_COLUMN = 1
_DEFAULT_NAME_WIDTH = 200


class HeaderTable(QTableWidget):
    """Read-only table for displaying HTTP headers with built-in filtering.

    Headers are sorted alphabetically by name and displayed in a two-column table.
    Provides case-insensitive substring filtering for both header names and values.
    All updates are batched to prevent UI flickering.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._all_headers: dict[str, str] = {}
        self._init_table()

    def _init_table(self) -> None:
        """Initialize table configuration and appearance."""
        self.setColumnCount(_COLUMN_COUNT)
        self.setHorizontalHeaderLabels(["Header", "Value"])

        # Configure column sizing behavior
        self.horizontalHeader().setSectionResizeMode(
            _NAME_COLUMN, QHeaderView.ResizeMode.Interactive
        )
        self.horizontalHeader().setSectionResizeMode(_VALUE_COLUMN, QHeaderView.ResizeMode.Stretch)
        self.horizontalHeader().setDefaultSectionSize(_DEFAULT_NAME_WIDTH)

        # Configure appearance
        self.verticalHeader().setVisible(False)
        self.setAlternatingRowColors(True)

        # Configure interaction
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)

    def load(self, headers: dict[str, str]) -> None:
        """Load headers into the table.

        Headers are sorted alphabetically by name. An internal copy is kept
        for efficient filtering operations.

        Args:
            headers: Dictionary of header name → value pairs
        """
        if not isinstance(headers, dict):
            logger.warning("Expected dict for headers, got %s; ignoring", type(headers).__name__)
            self._all_headers = {}
        else:
            self._all_headers = dict(sorted(headers.items()))

        self._apply_filter("")

    def filter(self, text: str) -> None:
        """Filter visible rows by provided text.

        Performs case-insensitive substring matching on both header names
        and values. Empty text shows all headers.

        Args:
            text: Filter text (any substring of name or value)
        """
        self._apply_filter(text)

    def _apply_filter(self, text: str) -> None:
        """Apply filter to the table (internal implementation).

        All UI updates are batched with setUpdatesEnabled(False) to prevent
        flickering during large filter operations.

        Args:
            text: Filter text to apply
        """
        term = text.lower().strip()
        filtered_rows = self._get_filtered_rows(term)

        # Batch UI mutations to prevent flickering
        self.setUpdatesEnabled(False)
        try:
            self._populate_table(filtered_rows)
        finally:
            self.setUpdatesEnabled(True)

    def _get_filtered_rows(self, filter_term: str) -> list:
        """Get headers matching the filter term.

        Args:
            filter_term: Lowercase search term

        Returns:
            List of (name, value) tuples that match the filter
        """
        if not filter_term:
            return [(k, v) for k, v in self._all_headers.items()]

        return [
            (k, v)
            for k, v in self._all_headers.items()
            if filter_term in k.lower() or filter_term in str(v).lower()
        ]

    def _populate_table(self, rows: list) -> None:
        """Populate the table with the given rows.

        Args:
            rows: List of (name, value) tuples to display
        """
        self.setRowCount(len(rows))

        for row, (name, value) in enumerate(rows):
            self._set_row(row, name, value)

        self.resizeRowsToContents()

    def _set_row(self, row: int, name: str, value: str) -> None:
        """Set name and value for a single table row.

        Args:
            row: Row index
            name: Header name
            value: Header value (converted to string if needed)
        """
        self.setItem(row, _NAME_COLUMN, QTableWidgetItem(name))
        self.setItem(row, _VALUE_COLUMN, QTableWidgetItem(str(value)))
