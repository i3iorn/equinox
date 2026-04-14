"""Path-parameter table that auto-extracts ``{{param}}`` tokens from a URL.

The table has two columns: **Parameter** (read-only, auto-populated from the
URL template) and **Value** (editable by the user).  When the URL changes,
rows are added/removed to match the current set of tokens while preserving
any values the user has already entered for unchanged parameters.
"""

import logging
import re
from contextlib import contextmanager
from typing import Optional

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

logger = logging.getLogger(__name__)

# Matches both {{param}} (Equinox interpolation syntax) and bare {param}
# (OpenAPI style).  Named groups are extracted; duplicates are collapsed.
_PATH_PARAM_RE = re.compile(r"\{\{(\w+)\}\}|\{(\w+)\}")

# Safety ceiling: a URL with more path params than this is pathological and
# would produce an unusably tall table row-set.  Extras are dropped and logged.
_MAX_PATH_PARAMS: int = 50


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

@contextmanager
def _blocked(obj: QObject):
    """Block Qt signals on *obj* for the duration of the ``with`` block.

    Saves and restores the previous blocked state so nested calls are safe —
    exiting an inner ``_blocked`` context never accidentally re-enables signals
    that an outer context is still holding blocked.
    """
    was_blocked = obj.signalsBlocked()
    obj.blockSignals(True)
    try:
        yield
    finally:
        obj.blockSignals(was_blocked)


# ---------------------------------------------------------------------------
# Public utility
# ---------------------------------------------------------------------------

def extract_path_params(url: str) -> list[str]:
    """Return an ordered, deduplicated list of path-parameter names in *url*.

    Recognises ``{{name}}`` (Equinox style) and ``{name}`` (OpenAPI / RFC 6570).
    Results are capped at ``_MAX_PATH_PARAMS`` to prevent pathological URLs
    from producing thousands of table rows.
    """
    seen: set[str] = set()
    result: list[str] = []
    for m in _PATH_PARAM_RE.finditer(url):
        name = m.group(1) or m.group(2)
        if name not in seen:
            seen.add(name)
            result.append(name)
            if len(result) >= _MAX_PATH_PARAMS:
                logger.warning(
                    "URL has more than %d path params; remaining params ignored",
                    _MAX_PATH_PARAMS,
                )
                break
    return result


# ---------------------------------------------------------------------------
# Widget
# ---------------------------------------------------------------------------

class PathParamsTable(QTableWidget):
    """Two-column table that displays path parameters parsed from a URL.

    Signals
    -------
    paramsChanged()
        Emitted whenever a value cell is edited.
    """

    # Column indices — avoids magic numbers throughout the class.
    _COL_PARAM: int = 0
    _COL_VALUE: int = 1
    _COL_COUNT: int = 2

    paramsChanged = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(0, self._COL_COUNT, parent)
        self.setHorizontalHeaderLabels(["Parameter", "Value"])
        self.horizontalHeader().setSectionResizeMode(
            self._COL_PARAM, QHeaderView.ResizeMode.Interactive
        )
        self.horizontalHeader().setSectionResizeMode(
            self._COL_VALUE, QHeaderView.ResizeMode.Stretch
        )
        self.horizontalHeader().setDefaultSectionSize(160)
        self.verticalHeader().setVisible(False)
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)

        self._params: dict[str, str] = {}   # name → user-entered value
        self._ordered: list[str] = []        # current ordered param names

        self.itemChanged.connect(self._on_item_changed)

    # ── Public API ────────────────────────────────────────────────────

    def update_from_url(self, url: str) -> None:
        """Re-extract parameters from *url* and rebuild rows.

        Existing values for parameters that still appear in the URL are
        preserved; parameters that disappeared are removed.
        """
        new_names = extract_path_params(url)
        if new_names == self._ordered:
            return  # nothing changed — avoid a needless full rebuild

        old_values = dict(self._params)
        self._ordered = new_names
        self._params = {name: old_values.get(name, "") for name in new_names}
        self._rebuild_table()
        self.paramsChanged.emit()

    def get_data(self) -> dict[str, str]:
        """Return ``{param_name: value}`` for all parameters with non-empty values."""
        self._sync_from_table()
        return {k: v for k, v in self._params.items() if v}

    def get_all_data(self) -> dict[str, str]:
        """Return ``{param_name: value}`` for *all* parameters (including empty)."""
        self._sync_from_table()
        return dict(self._params)

    def set_data(self, data: dict[str, str]) -> None:
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
        with _blocked(self):
            self.setRowCount(0)

    # ── Internal ──────────────────────────────────────────────────────

    def _rebuild_table(self) -> None:
        """Rebuild table rows from ``_ordered`` / ``_params``."""
        with _blocked(self):
            self.setRowCount(0)
            for name in self._ordered:
                row = self.rowCount()
                self.insertRow(row)

                param_item = QTableWidgetItem(name)
                param_item.setFlags(
                    Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
                )  # read-only
                self.setItem(row, self._COL_PARAM, param_item)
                self.setItem(row, self._COL_VALUE,
                             QTableWidgetItem(self._params.get(name, "")))

    def _sync_from_table(self) -> None:
        """Read current cell values back into ``_params``.

        Belt-and-suspenders sync called before every ``get_data`` /
        ``get_all_data`` to ensure in-memory state matches the visible cells,
        even if an edge case caused ``_on_item_changed`` to miss an update.
        """
        for row in range(self.rowCount()):
            param_item = self.item(row, self._COL_PARAM)
            val_item = self.item(row, self._COL_VALUE)
            if param_item and val_item:
                self._params[param_item.text()] = val_item.text()

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() == self._COL_VALUE:
            param_item = self.item(item.row(), self._COL_PARAM)
            if param_item:
                self._params[param_item.text()] = item.text()
            self.paramsChanged.emit()
