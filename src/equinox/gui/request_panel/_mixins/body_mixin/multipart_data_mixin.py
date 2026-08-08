"""Multipart table data helpers for ``RequestPanel``."""

from __future__ import annotations

import logging
from typing import Any, cast

from PyQt6.QtWidgets import QComboBox, QFileDialog, QMessageBox, QTableWidgetItem, QWidget

from equinox.gui.file_ops import validate_selected_path

logger = logging.getLogger(__name__)


class MultipartDataMixin:
    """Manage multipart request rows and file selection."""

    def _as_qwidget(self) -> QWidget:
        """Return the host panel typed as QWidget for Qt dialog APIs."""
        return cast(QWidget, cast(object, self))

    def _multipart_add_row(self) -> None:
        """Insert a new multipart row."""
        table = getattr(self, "_multipart_table", None)
        if table is None:
            raise RuntimeError("Multipart table unavailable")
        try:
            row = table.rowCount()
            table.insertRow(row)
            table.setItem(row, 0, QTableWidgetItem(""))
            type_combo = QComboBox()
            type_combo.addItems(["text", "file"])
            table.setCellWidget(row, 1, type_combo)
            table.setItem(row, 2, QTableWidgetItem(""))
        except RuntimeError:
            raise
        except Exception:
            logger.exception("Failed to add multipart row", exc_info=True)

    def _multipart_remove_row(self) -> None:
        """Remove the selected multipart rows."""
        table = getattr(self, "_multipart_table", None)
        if table is None:
            raise RuntimeError("Multipart table unavailable")
        try:
            rows = sorted({index.row() for index in table.selectedIndexes()}, reverse=True)
            for row in rows:
                table.removeRow(row)
        except RuntimeError:
            raise
        except Exception:
            logger.exception("Failed to remove multipart row", exc_info=True)

    def _multipart_browse_file(self) -> None:
        """Pick a multipart file path and write it into the selected row."""
        table = getattr(self, "_multipart_table", None)
        if table is None:
            raise RuntimeError("Multipart table unavailable")
        try:
            selected_row = table.currentRow()
            if selected_row < 0:
                return
            raw_path, _ = QFileDialog.getOpenFileName(
                self._as_qwidget(),
                "Select file to upload",
                "",
                "All files (*)",
            )
            if not raw_path:
                return
            try:
                selected_path = validate_selected_path(raw_path, must_exist=True)
            except ValueError as exc:
                QMessageBox.warning(self._as_qwidget(), "Invalid File", str(exc))
                return
            item = table.item(selected_row, 2)
            if item is None:
                table.setItem(selected_row, 2, QTableWidgetItem(str(selected_path)))
                return
            item.setText(str(selected_path))
        except RuntimeError:
            raise
        except Exception:
            logger.exception("Failed to browse multipart file", exc_info=True)

    def _get_multipart_data(self) -> list[dict[str, str]]:
        """Return the multipart rows as ``{key, type, value}`` dictionaries."""
        table = getattr(self, "_multipart_table", None)
        if table is None:
            return []
        output: list[dict[str, str]] = []
        try:
            for row in range(table.rowCount()):
                key_item = table.item(row, 0)
                if key_item is None or not key_item.text().strip():
                    continue
                output.append(
                    {
                        "key": key_item.text().strip(),
                        "type": self._multipart_cell_type(table, row),
                        "value": self._multipart_cell_value(table, row),
                    },
                )
        except Exception:
            logger.exception("Failed to read multipart data", exc_info=True)
        return output

    @staticmethod
    def _multipart_cell_type(table: Any, row: int) -> str:
        """Return the multipart row type from widget or fallback item text."""
        widget = table.cellWidget(row, 1)
        if isinstance(widget, QComboBox):
            return widget.currentText()
        item = table.item(row, 1)
        return item.text() if item is not None else "text"

    @staticmethod
    def _multipart_cell_value(table: Any, row: int) -> str:
        """Return the multipart row value cell text."""
        item = table.item(row, 2)
        return item.text() if item is not None else ""

    def _set_multipart_data(self, data: list[dict[str, str]] | None) -> None:
        """Replace multipart rows with the provided data."""
        table = getattr(self, "_multipart_table", None)
        if table is None:
            raise RuntimeError("Multipart table unavailable")
        try:
            table.setRowCount(0)
            for entry in data or []:
                self._append_multipart_entry(table, entry)
        except Exception:
            logger.exception("Failed to set multipart data", exc_info=True)

    @staticmethod
    def _append_multipart_entry(table: Any, entry: dict[str, str]) -> None:
        """Append one multipart entry to the table."""
        row = table.rowCount()
        table.insertRow(row)
        table.setItem(row, 0, QTableWidgetItem(str(entry.get("key", ""))))
        type_combo = QComboBox()
        type_combo.addItems(["text", "file"])
        entry_type = entry.get("type", "text")
        index = type_combo.findText(entry_type)
        if index >= 0:
            type_combo.setCurrentIndex(index)
        table.setCellWidget(row, 1, type_combo)
        table.setItem(row, 2, QTableWidgetItem(str(entry.get("value", ""))))
