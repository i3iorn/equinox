"""Global variables section for VariablesPanel."""

from __future__ import annotations

import logging

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from equinox.core.exceptions import ValidationError
from equinox.core.validation import Validator

from ..ui_common import confirm_yes_no, create_muted_label
from .variable_dialog import VariableDialog

logger = logging.getLogger(__name__)

_GLOBAL_TABLE_MAX_VISIBLE_ROWS = 3
_GLOBAL_TABLE_MIN_VISIBLE_ROWS = 1


class _GlobalVarsMixin:
    """Mixin providing the Global Variables section UI and CRUD logic."""

    def _build_global_vars_section(self) -> QGroupBox:
        """Construct the Global Variables group box and wire all signals.

        Assigns widget references to ``self`` so handler methods can reach them.
        Returns the constructed ``QGroupBox``.
        """
        self._global_group = QGroupBox("Global Variables")
        self._global_group.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum
        )
        global_layout = QVBoxLayout(self._global_group)
        global_layout.setContentsMargins(4, 4, 4, 4)
        global_layout.setSpacing(4)

        self._magic_hint = create_muted_label(
            "Built-in magic vars: {{TODAY}}, {{ONE_MONTH_AGO}}, {{ONE_YEAR_AGO}}, {{NOW_ISO}}"
        )
        self._magic_hint.setWordWrap(True)
        global_layout.addWidget(self._magic_hint)

        global_toolbar = QHBoxLayout()
        self._global_add_btn = QPushButton("Add")
        self._global_add_btn.clicked.connect(self._add_global_var)
        self._global_edit_btn = QPushButton("Edit")
        self._global_edit_btn.clicked.connect(self._edit_global_var)
        self._global_edit_btn.setEnabled(False)
        self._global_delete_btn = QPushButton("Delete")
        self._global_delete_btn.clicked.connect(self._delete_global_var)
        self._global_delete_btn.setEnabled(False)
        global_toolbar.addWidget(self._global_add_btn)
        global_toolbar.addWidget(self._global_edit_btn)
        global_toolbar.addWidget(self._global_delete_btn)
        global_toolbar.addStretch()
        global_layout.addLayout(global_toolbar)

        self._global_table = QTableWidget()
        self._global_table.setColumnCount(3)
        self._global_table.setHorizontalHeaderLabels(["Variable", "Value", "Description"])
        self._global_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self._global_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self._global_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        self._global_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._global_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._global_table.itemSelectionChanged.connect(self._on_global_selection)
        self._global_table.itemDoubleClicked.connect(self._edit_global_var)
        self._global_table.verticalHeader().setVisible(False)
        self._global_table.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum
        )
        global_layout.addWidget(self._global_table)

        return self._global_group

    # ── Height helpers ────────────────────────────────────────────────────────

    def _global_table_target_height(self) -> int:
        """Return a compact, content-based height for the global variables table."""
        header_h = self._global_table.horizontalHeader().height()
        frame_h = self._global_table.frameWidth() * 2
        row_h = self._global_table.verticalHeader().defaultSectionSize()
        visible_rows = max(
            _GLOBAL_TABLE_MIN_VISIBLE_ROWS,
            min(self._global_var_count, _GLOBAL_TABLE_MAX_VISIBLE_ROWS),
        )
        return frame_h + header_h + (row_h * visible_rows) + 2

    def _resize_global_table_to_content(self) -> None:
        """Keep the global variables table compact while still allowing scrolling."""
        self._global_table.setFixedHeight(self._global_table_target_height())

    # ── Data refresh ──────────────────────────────────────────────────────────

    def refresh_global_vars(self) -> None:
        """Rebuild the global variables table from the database."""
        try:
            rows = self._global_mgr.list_variables()
        except Exception as exc:
            logger.error("Failed to load global variables: %s", exc, exc_info=True)
            return

        self._global_var_count = len(rows)

        self._global_table.setUpdatesEnabled(False)
        try:
            self._global_table.setRowCount(len(rows))
            for row, var in enumerate(rows):
                key_item = QTableWidgetItem(var["key"])
                key_item.setData(Qt.ItemDataRole.UserRole, var["id"])
                value_item = QTableWidgetItem(var["value"])
                desc_item = QTableWidgetItem(var.get("description") or "")
                self._global_table.setItem(row, 0, key_item)
                self._global_table.setItem(row, 1, value_item)
                self._global_table.setItem(row, 2, desc_item)
        finally:
            self._global_table.setUpdatesEnabled(True)

        self._on_global_selection()
        self._resize_global_table_to_content()

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_global_selection(self) -> None:
        has_selection = bool(self._global_table.selectedItems())
        self._global_edit_btn.setEnabled(has_selection)
        self._global_delete_btn.setEnabled(has_selection)

    def _add_global_var(self) -> None:
        dialog = VariableDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        key, value, description = dialog.get_values()
        try:
            key = Validator.validate_variable_name(key)
            self._global_mgr.set_variable(key, value, description)
            self.refresh_global_vars()
            self.variables_changed.emit()
        except ValidationError as exc:
            QMessageBox.warning(self, "Validation", str(exc))
        except Exception as exc:
            logger.error("Failed to add global variable %r: %s", key, exc, exc_info=True)
            QMessageBox.critical(self, "Error", f"Failed to add global variable: {exc}")

    def _edit_global_var(self) -> None:
        row = self._global_table.currentRow()
        if row < 0:
            return
        key = self._global_table.item(row, 0).text()
        value = self._global_table.item(row, 1).text()
        description = self._global_table.item(row, 2).text()
        dialog = VariableDialog(self, key, value, description)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        new_key, new_value, new_description = dialog.get_values()
        try:
            new_key = Validator.validate_variable_name(new_key)
            if new_key != key:
                self._global_mgr.remove_variable(key)
            self._global_mgr.set_variable(new_key, new_value, new_description)
            self.refresh_global_vars()
            self.variables_changed.emit()
        except ValidationError as exc:
            QMessageBox.warning(self, "Validation", str(exc))
        except Exception as exc:
            logger.error("Failed to edit global variable %r: %s", key, exc, exc_info=True)
            QMessageBox.critical(self, "Error", f"Failed to edit global variable: {exc}")

    def _delete_global_var(self) -> None:
        row = self._global_table.currentRow()
        if row < 0:
            return
        key = self._global_table.item(row, 0).text()
        if not confirm_yes_no(self, "Confirm Delete", f"Delete global variable '{key}'?"):
            return
        try:
            key = Validator.validate_variable_name(key)
            self._global_mgr.remove_variable(key)
            self.refresh_global_vars()
            self.variables_changed.emit()
        except Exception as exc:
            logger.error("Failed to delete global variable %r: %s", key, exc, exc_info=True)
            QMessageBox.critical(self, "Error", f"Failed to delete global variable: {exc}")

