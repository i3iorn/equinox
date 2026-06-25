"""Capture-table helpers for ``RequestPanel``."""
from __future__ import annotations

from typing import cast

from PyQt6.QtWidgets import QComboBox, QHeaderView, QLabel, QTableWidget, QTableWidgetItem, QWidget

from equinox.gui.request_panel._mixins.assertions_mixin import AssertionsMixin

_CAPTURE_SOURCES: tuple[str, ...] = ("json", "header", "regex", "status")
_LABEL_CAPTURES = "Captures"
_LABEL_LAST_CAPTURE = "Last capture results:"


class CapturesMixin(AssertionsMixin):
    """Create, populate, and read response capture rules."""

    def _create_captures_tab(self) -> QWidget:
        """Create the captures tab and results area."""
        widget, layout, self.captures_results_label = self._build_action_tab_shell(
            _LABEL_CAPTURES,
            self._captures_add_row,
            self._captures_remove_row,
        )
        self.captures_table = self._create_captures_table()
        layout.addWidget(self.captures_table)
        layout.addWidget(QLabel(_LABEL_LAST_CAPTURE))
        layout.addWidget(self.captures_results_label)
        return widget

    @staticmethod
    def _create_captures_table() -> QTableWidget:
        """Create the capture rules table."""
        table = QTableWidget(0, 4)
        table.setHorizontalHeaderLabels(["Variable", "Source", "Path / Pattern", "Default"])
        header = table.horizontalHeader()
        if header is not None:
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
            header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        v_header = table.verticalHeader()
        if v_header is not None:
            v_header.setVisible(False)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        return table

    def _captures_add_row(self) -> None:
        """Append an empty capture row."""
        row = self.captures_table.rowCount()
        self.captures_table.insertRow(row)
        self.captures_table.setItem(row, 0, QTableWidgetItem(""))
        source_combo = QComboBox()
        source_combo.addItems(_CAPTURE_SOURCES)
        self.captures_table.setCellWidget(row, 1, source_combo)
        self.captures_table.setItem(row, 2, QTableWidgetItem(""))
        self.captures_table.setItem(row, 3, QTableWidgetItem(""))

    def _captures_remove_row(self) -> None:
        """Remove the selected capture rows."""
        self._remove_selected_from(self.captures_table)

    def _get_captures(self) -> list[dict[str, str]]:
        """Collect captures from the table."""
        captures: list[dict[str, str]] = []
        for row in range(self.captures_table.rowCount()):
            variable_item = self.captures_table.item(row, 0)
            variable = variable_item.text().strip() if variable_item else ""
            if not variable:
                continue
            source_widget = self.captures_table.cellWidget(row, 1)
            source_combo = cast(QComboBox | None, source_widget)
            source = source_combo.currentText() if source_combo else "json"
            path_item = self.captures_table.item(row, 2)
            default_item = self.captures_table.item(row, 3)
            captures.append(
                {
                    "variable": variable,
                    "source": source,
                    "path": path_item.text().strip() if path_item else "",
                    "default": default_item.text().strip() if default_item else "",
                },
            )
        return captures

    def _set_captures(self, captures: list[dict[str, str]] | None) -> None:
        """Populate the captures table from saved rules."""
        self.captures_table.setRowCount(0)
        for capture in captures or []:
            row = self.captures_table.rowCount()
            self.captures_table.insertRow(row)
            self.captures_table.setItem(row, 0, QTableWidgetItem(capture.get("variable", "")))
            source_combo = QComboBox()
            source_combo.addItems(_CAPTURE_SOURCES)
            source = capture.get("source", "json")
            index = source_combo.findText(source)
            if index >= 0:
                source_combo.setCurrentIndex(index)
            self.captures_table.setCellWidget(row, 1, source_combo)
            self.captures_table.setItem(row, 2, QTableWidgetItem(capture.get("path", "")))
            self.captures_table.setItem(row, 3, QTableWidgetItem(capture.get("default", "")))
