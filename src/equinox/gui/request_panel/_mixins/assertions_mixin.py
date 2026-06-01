"""Assertion-table helpers for ``RequestPanel``."""
from __future__ import annotations

from typing import Any
from typing import cast

from equinox.core.assertions import evaluate_assertion as _evaluate_assertion
from equinox.gui.theme import get_mono_font
from PyQt6.QtWidgets import QComboBox
from PyQt6.QtWidgets import QHeaderView
from PyQt6.QtWidgets import QLabel
from PyQt6.QtWidgets import QPushButton
from PyQt6.QtWidgets import QTableWidget
from PyQt6.QtWidgets import QTableWidgetItem
from PyQt6.QtWidgets import QVBoxLayout
from PyQt6.QtWidgets import QWidget

_ASSERTION_TYPES: tuple[str, ...] = (
    "status",
    "body_contains",
    "header_value",
    "jsonpath",
    "elapsed_lt",
)

_LABEL_ASSERTIONS = "Assertions"
_LABEL_LAST_ASSERTION = "Last assertion results:"
_LABEL_EMPTY = "ÔÇö"
_LAYOUT_MARGINS = (0, 4, 0, 0)
_TOOLBAR_MARGINS = (0, 2, 0, 0)
_TOOLBAR_SPACING = 2
_BUTTON_ADD_WIDTH = 64
_BUTTON_REMOVE_WIDTH = 80


class AssertionsMixin:
    """Create, populate, and evaluate response assertions."""

    tabs: Any

    @staticmethod
    def _build_action_tab_shell(title: str, add_slot: Any, remove_slot: Any) -> tuple[QWidget, QVBoxLayout, QLabel]:
        """Build a shared shell for assertion and capture tabs."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(*_LAYOUT_MARGINS)

        from PyQt6.QtWidgets import QHBoxLayout

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(*_TOOLBAR_MARGINS)
        toolbar.setSpacing(_TOOLBAR_SPACING)
        toolbar.addWidget(QLabel(title))

        add_button = QPushButton("+ Add")
        add_button.setMinimumWidth(_BUTTON_ADD_WIDTH)
        add_button.clicked.connect(add_slot)
        remove_button = QPushButton("ÔêÆ Remove")
        remove_button.setMinimumWidth(_BUTTON_REMOVE_WIDTH)
        remove_button.clicked.connect(remove_slot)

        toolbar.addWidget(add_button)
        toolbar.addWidget(remove_button)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        results_label = QLabel(_LABEL_EMPTY)
        results_label.setFont(get_mono_font())
        results_label.setWordWrap(True)
        results_label.setObjectName("mutedLabel")
        return widget, layout, results_label

    @staticmethod
    def _remove_selected_from(table: QTableWidget) -> None:
        """Remove selected rows from a table from bottom to top."""
        for row in sorted({index.row() for index in table.selectedIndexes()}, reverse=True):
            table.removeRow(row)

    def _create_assertions_tab(self) -> QWidget:
        """Create the assertions tab and results area."""
        widget, layout, self.assertions_results_label = self._build_action_tab_shell(
            _LABEL_ASSERTIONS,
            self._assertions_add_row,
            self._assertions_remove_row,
        )
        self.assertions_table = self._create_assertions_table()
        layout.addWidget(self.assertions_table)
        layout.addWidget(QLabel(_LABEL_LAST_ASSERTION))
        layout.addWidget(self.assertions_results_label)
        return widget

    @staticmethod
    def _create_assertions_table() -> QTableWidget:
        """Create the assertion rules table."""
        table = QTableWidget(0, 3)
        table.setHorizontalHeaderLabels(["Type", "Field / Path", "Expected"])
        header = table.horizontalHeader()
        if header is not None:
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
            header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
            header.setDefaultSectionSize(160)
        v_header = table.verticalHeader()
        if v_header is not None:
            v_header.setVisible(False)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        return table

    def _assertions_add_row(self) -> None:
        """Append an empty assertion row."""
        row = self.assertions_table.rowCount()
        self.assertions_table.insertRow(row)
        type_combo = QComboBox()
        type_combo.addItems(_ASSERTION_TYPES)
        self.assertions_table.setCellWidget(row, 0, type_combo)
        self.assertions_table.setItem(row, 1, QTableWidgetItem(""))
        self.assertions_table.setItem(row, 2, QTableWidgetItem(""))

    def _assertions_remove_row(self) -> None:
        """Remove the selected assertion rows."""
        self._remove_selected_from(self.assertions_table)

    def _get_assertions(self) -> list[dict[str, str]]:
        """Collect assertion rules from the table."""
        rules: list[dict[str, str]] = []
        for row in range(self.assertions_table.rowCount()):
            widget = self.assertions_table.cellWidget(row, 0)
            combo = cast(QComboBox | None, widget)
            assertion_type = combo.currentText() if combo else "status"
            field_item = self.assertions_table.item(row, 1)
            expected_item = self.assertions_table.item(row, 2)
            field = field_item.text().strip() if field_item else ""
            expected = expected_item.text().strip() if expected_item else ""
            if expected:
                rules.append({"type": assertion_type, "field": field, "expected": expected})
        return rules

    def _set_assertions(self, rules: list[dict[str, str]] | None) -> None:
        """Populate the assertions table from saved rules."""
        self.assertions_table.setRowCount(0)
        for rule in rules or []:
            self._assertions_add_row()
            row = self.assertions_table.rowCount() - 1
            widget = self.assertions_table.cellWidget(row, 0)
            combo = cast(QComboBox | None, widget)
            if combo:
                index = combo.findText(rule.get("type", "status"))
                if index >= 0:
                    combo.setCurrentIndex(index)
            field_item = self.assertions_table.item(row, 1)
            expected_item = self.assertions_table.item(row, 2)
            if field_item is not None:
                field_item.setText(rule.get("field", ""))
            if expected_item is not None:
                expected_item.setText(rule.get("expected", ""))

    def _evaluate_assertions(self, response: Any) -> None:
        """Evaluate all assertions against a response and update the UI."""
        rules = self._get_assertions()
        if not rules:
            self.assertions_results_label.setText(_LABEL_EMPTY)
            return
        lines = [self._render_assertion_line(rule, response) for rule in rules]
        self.assertions_results_label.setText("\n".join(lines) if lines else _LABEL_EMPTY)
        self._update_assertions_tab_label(lines)

    def _render_assertion_line(self, rule: dict[str, str], response: Any) -> str:
        """Return a rendered assertion result line."""
        passed, message = _evaluate_assertion(rule, response)
        icon = "Ô£ô" if passed else "Ô£ù"
        return f"{icon} {message}"

    def _update_assertions_tab_label(self, lines: list[str]) -> None:
        """Reflect assertion pass/fail counts in the tab label."""
        passed_count = sum(1 for line in lines if line.startswith("Ô£ô"))
        total = len(lines)
        label = f"{_LABEL_ASSERTIONS} ({passed_count}/{total})" if lines else _LABEL_ASSERTIONS
        for index in range(self.tabs.count()):
            if self.tabs.tabText(index).startswith(_LABEL_ASSERTIONS):
                self.tabs.setTabText(index, label)
                return
