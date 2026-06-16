"""Assertion-table helpers for RequestPanel.

This module provides a deterministic, auditable, and security‑focused
implementation of assertion table management for the GUI. All functions
follow zero‑trust principles and avoid hidden side effects.
"""
from __future__ import annotations

from typing import Any
from typing import cast
from typing import Optional

from equinox.core.assertions import evaluate_assertion as evaluate_assertion_safe
from equinox.gui.theme import get_mono_font
from PyQt6.QtWidgets import QComboBox
from PyQt6.QtWidgets import QHeaderView
from PyQt6.QtWidgets import QLabel
from PyQt6.QtWidgets import QPushButton
from PyQt6.QtWidgets import QTableWidget
from PyQt6.QtWidgets import QTableWidgetItem
from PyQt6.QtWidgets import QVBoxLayout
from PyQt6.QtWidgets import QWidget


# ─────────────────────────────────────────────────────────────────────────────
# Immutable Constants
# ─────────────────────────────────────────────────────────────────────────────

ASSERTION_TYPES: tuple[str, ...] = (
    "status",
    "body_contains",
    "header_value",
    "jsonpath",
    "elapsed_lt",
)

LABEL_ASSERTIONS: str = "Assertions"
LABEL_LAST_ASSERTION: str = "Last assertion results:"
LABEL_EMPTY: str = "—"

LAYOUT_MARGINS: tuple[int, int, int, int] = (0, 4, 0, 0)
TOOLBAR_MARGINS: tuple[int, int, int, int] = (0, 2, 0, 0)
TOOLBAR_SPACING: int = 2

BUTTON_ADD_WIDTH: int = 64
BUTTON_REMOVE_WIDTH: int = 80


# ─────────────────────────────────────────────────────────────────────────────
# Assertions Mixin
# ─────────────────────────────────────────────────────────────────────────────

class AssertionsMixin:
    """Create, populate, and evaluate response assertions.

    This mixin is intentionally deterministic and side‑effect‑transparent.
    All UI mutations are explicit. No function performs hidden state changes.
    """

    tabs: Any  # GUI container; type depends on parent widget
    assertions_table: Any
    assertions_results_label: Any

    # ──────────────────────────────────────────────────────────────────────
    # Tab Construction
    # ──────────────────────────────────────────────────────────────────────

    def _create_assertions_tab(self) -> QWidget:
        """Create the Assertions tab and wire table/results widgets."""
        widget, layout, self.assertions_results_label = self._build_action_tab_shell(
            LABEL_ASSERTIONS,
            self._assertions_add_row,
            self._assertions_remove_row,
        )
        self.assertions_table = self._create_assertions_table()
        layout.addWidget(self.assertions_table)
        layout.addWidget(QLabel(LABEL_LAST_ASSERTION))
        layout.addWidget(self.assertions_results_label)
        return widget

    @staticmethod
    def _build_action_tab_shell(
        title: str,
        add_handler: Any,
        remove_handler: Any,
    ) -> tuple[QWidget, QVBoxLayout, QLabel]:
        """Build a shared shell for assertion and capture tabs.

        Args:
            title: Section title.
            add_handler: Slot for adding rows.
            remove_handler: Slot for removing rows.

        Returns:
            A tuple of (widget, layout, results_label).
        """
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(*LAYOUT_MARGINS)

        from PyQt6.QtWidgets import QHBoxLayout

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(*TOOLBAR_MARGINS)
        toolbar.setSpacing(TOOLBAR_SPACING)
        toolbar.addWidget(QLabel(title))

        add_button = QPushButton("+ Add")
        add_button.setMinimumWidth(BUTTON_ADD_WIDTH)
        add_button.clicked.connect(add_handler)

        remove_button = QPushButton("␡ Remove")
        remove_button.setMinimumWidth(BUTTON_REMOVE_WIDTH)
        remove_button.clicked.connect(remove_handler)

        toolbar.addWidget(add_button)
        toolbar.addWidget(remove_button)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        results_label = QLabel(LABEL_EMPTY)
        results_label.setFont(get_mono_font())
        results_label.setWordWrap(True)
        results_label.setObjectName("mutedLabel")

        return widget, layout, results_label

    # ──────────────────────────────────────────────────────────────────────
    # Table Helpers
    # ──────────────────────────────────────────────────────────────────────

    @staticmethod
    def _remove_selected_from(table: QTableWidget) -> None:
        """Remove selected rows from a table in a deterministic order."""
        selected_rows = {index.row() for index in table.selectedIndexes()}
        for row in sorted(selected_rows, reverse=True):
            table.removeRow(row)

    @staticmethod
    def _create_assertions_table() -> QTableWidget:
        """Create the assertion rules table with secure defaults."""
        table = QTableWidget(0, 3)
        table.setHorizontalHeaderLabels(["Type", "Field / Path", "Expected"])

        header = table.horizontalHeader()
        if header:
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
            header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
            header.setDefaultSectionSize(160)

        v_header = table.verticalHeader()
        if v_header:
            v_header.setVisible(False)

        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        return table

    # ──────────────────────────────────────────────────────────────────────
    # Row Management
    # ──────────────────────────────────────────────────────────────────────

    def _assertions_add_row(self) -> None:
        """Append an empty assertion row."""
        row = self.assertions_table.rowCount()
        self.assertions_table.insertRow(row)

        type_combo = QComboBox()
        type_combo.addItems(ASSERTION_TYPES)
        self.assertions_table.setCellWidget(row, 0, type_combo)

        self.assertions_table.setItem(row, 1, QTableWidgetItem(""))
        self.assertions_table.setItem(row, 2, QTableWidgetItem(""))

    def _assertions_remove_row(self) -> None:
        """Remove the selected assertion rows."""
        self._remove_selected_from(self.assertions_table)

    # ──────────────────────────────────────────────────────────────────────
    # Serialization / Deserialization
    # ──────────────────────────────────────────────────────────────────────

    def _get_assertions(self) -> list[dict[str, str]]:
        """Collect assertion rules from the table.

        Returns:
            A list of assertion rule dictionaries.
        """
        rules: list[dict[str, str]] = []

        for row in range(self.assertions_table.rowCount()):
            widget = self.assertions_table.cellWidget(row, 0)
            combo = cast(Optional[QComboBox], widget)

            assertion_type = combo.currentText() if combo else "status"

            field_item = self.assertions_table.item(row, 1)
            expected_item = self.assertions_table.item(row, 2)

            field = field_item.text().strip() if field_item else ""
            expected = expected_item.text().strip() if expected_item else ""

            if expected:
                rules.append(
                    {
                        "type": assertion_type,
                        "field": field,
                        "expected": expected,
                    },
                )

        return rules

    def _set_assertions(self, rules: list[dict[str, str]] | None) -> None:
        """Populate the assertions table from saved rules."""
        self.assertions_table.setRowCount(0)

        for rule in rules or []:
            self._assertions_add_row()
            row = self.assertions_table.rowCount() - 1

            combo = cast(Optional[QComboBox], self.assertions_table.cellWidget(row, 0))
            if combo:
                index = combo.findText(rule.get("type", "status"))
                if index >= 0:
                    combo.setCurrentIndex(index)

            field_item = self.assertions_table.item(row, 1)
            expected_item = self.assertions_table.item(row, 2)

            if field_item:
                field_item.setText(rule.get("field", ""))

            if expected_item:
                expected_item.setText(rule.get("expected", ""))

    # ──────────────────────────────────────────────────────────────────────
    # Evaluation
    # ──────────────────────────────────────────────────────────────────────

    def _evaluate_assertions(self, response: Any) -> None:
        """Evaluate all assertions against a response and update the UI."""
        rules = self._get_assertions()

        if not rules:
            self.assertions_results_label.setText(LABEL_EMPTY)
            return

        lines = [self._render_assertion_line(rule, response) for rule in rules]
        self.assertions_results_label.setText("\n".join(lines) if lines else LABEL_EMPTY)

        self._update_assertions_tab_label(lines)

    def _render_assertion_line(self, rule: dict[str, str], response: Any) -> str:
        """Render a single assertion result line."""
        passed, message = evaluate_assertion_safe(rule, response)
        icon = "✓" if passed else "✗"
        return f"{icon} {message}"

    def _update_assertions_tab_label(self, lines: list[str]) -> None:
        """Reflect assertion pass/fail counts in the tab label."""
        passed_count = sum(1 for line in lines if line.startswith("✓"))
        total = len(lines)

        label = f"{LABEL_ASSERTIONS} ({passed_count}/{total})" if lines else LABEL_ASSERTIONS

        for index in range(self.tabs.count()):
            if self.tabs.tabText(index).startswith(LABEL_ASSERTIONS):
                self.tabs.setTabText(index, label)
                return
