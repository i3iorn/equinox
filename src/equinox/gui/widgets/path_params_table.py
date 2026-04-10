"""Path-parameter table that auto-extracts ``{{param}}`` tokens from a URL.

The table has two columns: **Parameter** (read-only, auto-populated from the
URL template) and **Value** (editable by the user).  When the URL changes,
rows are added/removed to match the current set of tokens while preserving
any values the user has already entered for unchanged parameters.
"""

import logging
import re
from typing import Dict

from PyQt6.QtWidgets import (
    QTableWidget, QTableWidgetItem, QHeaderView,
)
from PyQt6.QtCore import Qt, pyqtSignal

logger = logging.getLogger(__name__)


# Matches both {{param}} (Equinox interpolation syntax) and bare {param}
# (OpenAPI style).  Named groups are extracted; duplicates are collapsed.
_PATH_PARAM_RE = re.compile(r"\{\{(\w+)\}\}|\{(\w+)\}")


def extract_path_params(url: str) -> list:
    """Return an ordered, deduplicated list of path-parameter names in *url*.

    Recognises ``{{name}}`` (Equinox style) and ``{name}`` (OpenAPI / RFC 6570).
    """
    seen: set = set()
    result: list = []
    for m in _PATH_PARAM_RE.finditer(url):
        name = m.group(1) or m.group(2)
        if name not in seen:
            seen.add(name)
            result.append(name)
    return result


class PathParamsTable(QTableWidget):
    """Two-column table that displays path parameters parsed from a URL.

    Signals
    -------
    paramsChanged()
        Emitted whenever a value cell is edited.
    """

    paramsChanged = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(0, 2, parent)
        self.setHorizontalHeaderLabels(["Parameter", "Value"])
        self.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Interactive
        )
        self.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.horizontalHeader().setDefaultSectionSize(160)
        self.verticalHeader().setVisible(False)
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)

        self._params: Dict[str, str] = {}   # name → user-entered value
        self._ordered: list = []             # current ordered param names

        self.itemChanged.connect(self._on_item_changed)

    # ── Public API ────────────────────────────────────────────────────

    def update_from_url(self, url: str) -> None:
        """Re-extract parameters from *url* and rebuild rows.

        Existing values for parameters that still appear in the URL are
        preserved; parameters that disappeared are removed.
        """
        new_names = extract_path_params(url)
        if new_names == self._ordered:
            return  # nothing changed

        # Preserve existing values
        old_values = dict(self._params)

        self._ordered = new_names
        self._params = {
            name: old_values.get(name, "")
            for name in new_names
        }
        self._rebuild_table()
        self.paramsChanged.emit()

    def get_data(self) -> Dict[str, str]:
        """Return ``{param_name: value}`` for all parameters with non-empty values."""
        self._sync_from_table()
        return {k: v for k, v in self._params.items() if v}

    def get_all_data(self) -> Dict[str, str]:
        """Return ``{param_name: value}`` for *all* parameters (including empty)."""
        self._sync_from_table()
        return dict(self._params)

    def set_data(self, data: Dict[str, str]) -> None:
        """Load saved path-parameter values (e.g. from the database).

        Safe to call both *before* and *after* ``update_from_url``.  When
        called after, the visible value cells are refreshed immediately so the
        loaded values are shown without requiring a URL change.
        """
        if not data:
            return
        self._params.update(data)
        # If the table is already populated, refresh value cells so the caller
        # does not have to worry about call ordering with update_from_url.
        if self._ordered:
            self._rebuild_table()

    def reset(self) -> None:
        """Clear all parameters and rows."""
        self._params.clear()
        self._ordered.clear()
        self.blockSignals(True)
        try:
            self.setRowCount(0)
        finally:
            self.blockSignals(False)

    # ── Internal ──────────────────────────────────────────────────────

    def _rebuild_table(self) -> None:
        """Rebuild table rows from ``_ordered`` / ``_params``."""
        self.blockSignals(True)
        try:
            self.setRowCount(0)
            for name in self._ordered:
                row = self.rowCount()
                self.insertRow(row)

                key_item = QTableWidgetItem(name)
                key_item.setFlags(
                    Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
                )  # read-only
                self.setItem(row, 0, key_item)

                val_item = QTableWidgetItem(self._params.get(name, ""))
                self.setItem(row, 1, val_item)
        except Exception:
            logger.exception("_rebuild_table failed for params %s", self._ordered)
        finally:
            # Always restore the blocked state so the table never becomes
            # permanently deaf to itemChanged signals after an exception.
            self.blockSignals(False)

    def _sync_from_table(self) -> None:
        """Read current cell values back into ``_params``."""
        for row in range(self.rowCount()):
            key_item = self.item(row, 0)
            val_item = self.item(row, 1)
            if key_item and val_item:
                self._params[key_item.text()] = val_item.text()

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() == 1:
            key_item = self.item(item.row(), 0)
            if key_item:
                self._params[key_item.text()] = item.text()
            self.paramsChanged.emit()
