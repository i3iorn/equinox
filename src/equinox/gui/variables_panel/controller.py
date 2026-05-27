from __future__ import annotations

from typing import Any

from equinox.core.interpolation import VariableInterpolator
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QTableWidget
from PyQt6.QtWidgets import QTableWidgetItem


class VariablesTableController:
    """Responsible for populating and updating the variables table."""

    def __init__(self, table: QTableWidget) -> None:
        self.table = table

    def load(self, variables: list[dict[str, Any]], interp_ctx: dict[str, Any]) -> None:
        self.table.setSortingEnabled(False)
        self.table.setUpdatesEnabled(False)

        try:
            self.table.setRowCount(len(variables))

            for row, var in enumerate(variables):
                key = var["key"]
                raw = var.get("value") or ""
                desc = var.get("description") or ""

                key_item = QTableWidgetItem(key)
                key_item.setData(Qt.ItemDataRole.UserRole, var["id"])

                value_item = QTableWidgetItem(raw)
                desc_item = QTableWidgetItem(desc)

                self.table.setItem(row, 0, key_item)
                self.table.setItem(row, 1, value_item)
                self.table.setItem(row, 2, desc_item)

                try:
                    interpolated = VariableInterpolator.interpolate(raw, interp_ctx) if raw else ""
                except Exception:
                    interpolated = raw

                tooltip = (
                    f"Raw: {raw}\nInterpolated: {interpolated}" if interpolated != raw else raw
                )
                value_item.setToolTip(tooltip)
                key_item.setToolTip(f"{key} → {interpolated}")

        finally:
            self.table.setUpdatesEnabled(True)
            self.table.setSortingEnabled(True)
