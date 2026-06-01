"""Variable groups and group-scoped variables section for VariablesPanel."""
from __future__ import annotations

import logging
from typing import Any
from typing import cast
from typing import TYPE_CHECKING

from equinox.core.interpolation import VariableInterpolator
from PyQt6.QtCore import QSettings
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog
from PyQt6.QtWidgets import QHBoxLayout
from PyQt6.QtWidgets import QHeaderView
from PyQt6.QtWidgets import QInputDialog
from PyQt6.QtWidgets import QLabel
from PyQt6.QtWidgets import QListWidget
from PyQt6.QtWidgets import QListWidgetItem
from PyQt6.QtWidgets import QMenu
from PyQt6.QtWidgets import QMessageBox
from PyQt6.QtWidgets import QPushButton
from PyQt6.QtWidgets import QSplitter
from PyQt6.QtWidgets import QTableWidget
from PyQt6.QtWidgets import QTableWidgetItem
from PyQt6.QtWidgets import QVBoxLayout
from PyQt6.QtWidgets import QWidget

from ..ui_common import configure_splitter_persistence
from ..ui_common import confirm_yes_no
from .variable_dialog import VariableDialog

if TYPE_CHECKING:
    from ...storage import VariableGroupManager

logger = logging.getLogger(__name__)


class _GroupsMixin(QWidget):
    """Mixin providing variable groups and group-scoped variable CRUD logic."""

    if TYPE_CHECKING:
        variables_changed: Any
        _mgr: VariableGroupManager
        _settings: QSettings
        def _ordered_context_actions(self, menu_key: str, action_specs: Any) -> Any: ...
        def _run_context_action(self, menu_key: str, action_id: str, callback: Any) -> None: ...

    def _build_groups_section(self) -> QSplitter:
        """Construct the groups/variables splitter panel."""
        splitter = QSplitter(Qt.Orientation.Horizontal)

        left_widget = self._build_groups_panel()
        right_widget = self._build_variables_panel()

        splitter.addWidget(left_widget)
        splitter.addWidget(right_widget)

        self._configure_splitter(splitter)
        return splitter

    def _build_groups_panel(self) -> QWidget:
        """Create the left-side groups panel."""
        container = QWidget()
        layout = QVBoxLayout(container)

        layout.addWidget(QLabel("<b>Groups</b>"))
        layout.addLayout(self._build_groups_toolbar())
        layout.addWidget(self._build_groups_list())

        return container

    def _build_groups_toolbar(self) -> QHBoxLayout:
        """Create toolbar with group actions."""
        toolbar = QHBoxLayout()

        self.new_group_btn = QPushButton("New Group")
        self.new_group_btn.clicked.connect(self.create_group)

        self.delete_group_btn = QPushButton("Delete Group")
        self.delete_group_btn.clicked.connect(self.delete_group)
        self.delete_group_btn.setEnabled(False)

        toolbar.addWidget(self.new_group_btn)
        toolbar.addWidget(self.delete_group_btn)
        toolbar.addStretch()

        return toolbar

    def _build_groups_list(self) -> QListWidget:
        """Create and configure the groups list widget."""
        self.groups_list = QListWidget()
        self.groups_list.itemSelectionChanged.connect(self._on_group_selected)
        self.groups_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.groups_list.customContextMenuRequested.connect(self._show_group_context_menu)
        return self.groups_list

    def _build_variables_panel(self) -> QWidget:
        """Create the right-side variables panel."""
        container = QWidget()
        layout = QVBoxLayout(container)

        layout.addWidget(QLabel("<b>Variables</b>"))
        layout.addLayout(self._build_variables_toolbar())
        layout.addWidget(self._build_variables_table())

        return container

    def _build_variables_toolbar(self) -> QHBoxLayout:
        """Create toolbar with variable actions."""
        toolbar = QHBoxLayout()

        self.add_var_btn = QPushButton("Add Variable")
        self.add_var_btn.clicked.connect(self.add_variable)
        self.add_var_btn.setEnabled(False)

        self.edit_var_btn = QPushButton("Edit")
        self.edit_var_btn.clicked.connect(self.edit_variable)
        self.edit_var_btn.setEnabled(False)

        self.remove_var_btn = QPushButton("Remove")
        self.remove_var_btn.clicked.connect(self.remove_variable)
        self.remove_var_btn.setEnabled(False)

        toolbar.addWidget(self.add_var_btn)
        toolbar.addWidget(self.edit_var_btn)
        toolbar.addWidget(self.remove_var_btn)
        toolbar.addStretch()

        return toolbar

    def _build_variables_table(self) -> QTableWidget:
        """Create and configure the variables table."""
        self.variables_table = QTableWidget()
        self.variables_table.setColumnCount(3)
        self.variables_table.setHorizontalHeaderLabels(["Key", "Value", "Description"])

        header = self.variables_table.horizontalHeader()

        if header is not None:
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
            header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)

        self.variables_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.variables_table.itemSelectionChanged.connect(self._on_variable_selected)
        self.variables_table.itemDoubleClicked.connect(self.edit_variable)

        return self.variables_table

    def _configure_splitter(self, splitter: QSplitter) -> None:
        """Apply persistence and visual settings to the splitter."""
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(5)

        configure_splitter_persistence(
            splitter,
            settings_key="splitter/variables",
            default_sizes=[250, 550],
            settings=self._settings,
        )

    # ── Group data refresh ────────────────────────────────────────────────────

    def refresh_groups(self) -> None:
        """Rebuild the groups list from the database.

        Signals and screen updates are suppressed during the rebuild.
        """
        self.groups_list.blockSignals(True)
        self.groups_list.setUpdatesEnabled(False)
        try:
            self.groups_list.clear()
            self.current_group_id: int | None = None
            for group in self._mgr.list_groups():
                item = QListWidgetItem(group["name"])
                item.setData(Qt.ItemDataRole.UserRole, group["id"])
                if group["description"]:
                    item.setToolTip(group["description"])
                self.groups_list.addItem(item)
        except Exception as exc:
            logger.error("Failed to load variable groups: %s", exc, exc_info=True)
        finally:
            self.groups_list.setUpdatesEnabled(True)
            self.groups_list.blockSignals(False)

    def refresh_variables(self) -> None:
        """Rebuild the variables table for the currently selected group.

        The interpolation context is built once before the loop so every row
        reuses the same snapshot instead of making fresh DB/OS calls per row.
        Screen updates are suppressed during the rebuild.
        """
        if not self.current_group_id:
            self.variables_table.setRowCount(0)
            return

        try:
            db_vars = self._mgr.list_group_variables(self.current_group_id)
        except Exception as exc:
            logger.error(
                "Failed to load variables for group %s: %s",
                self.current_group_id,
                exc,
                exc_info=True,
            )
            return

        interp_vars = self._build_interp_context()

        self.variables_table.setSortingEnabled(False)
        self.variables_table.setUpdatesEnabled(False)
        try:
            self.variables_table.setRowCount(len(db_vars))
            for row, var in enumerate(db_vars):
                key_item = QTableWidgetItem(var["key"])
                key_item.setData(Qt.ItemDataRole.UserRole, var["id"])
                value_item = QTableWidgetItem(var["value"])
                desc_item = QTableWidgetItem(var["description"] or "")

                self.variables_table.setItem(row, 0, key_item)
                self.variables_table.setItem(row, 1, value_item)
                self.variables_table.setItem(row, 2, desc_item)

                raw = var.get("value") or ""
                try:
                    interpolated = VariableInterpolator.interpolate(raw, interp_vars) if raw else ""
                except Exception:
                    interpolated = raw
                if interpolated != raw:
                    value_item.setToolTip(f"Raw: {raw}\nInterpolated: {interpolated}")
                else:
                    value_item.setToolTip(raw)
                key_item.setToolTip(f"{var['key']} → {interpolated}")
        finally:
            self.variables_table.setUpdatesEnabled(True)
            self.variables_table.setSortingEnabled(True)

    # ── Group slots ───────────────────────────────────────────────────────────

    def _on_group_selected(self) -> None:
        selected = self.groups_list.selectedItems()
        if not selected:
            self.current_group_id = None
            self.delete_group_btn.setEnabled(False)
            self.add_var_btn.setEnabled(False)
            self.variables_table.setRowCount(0)
            return
        self.current_group_id = selected[0].data(Qt.ItemDataRole.UserRole)
        self.delete_group_btn.setEnabled(True)
        self.add_var_btn.setEnabled(True)
        self.refresh_variables()

    def create_group(self) -> None:
        parent = cast(QWidget, self)
        name, ok = QInputDialog.getText(parent, "New Variable Group", "Group name:")
        if not ok or not name:
            return
        description, ok = QInputDialog.getText(
            parent, "New Variable Group", "Description (optional):",
        )
        if not ok:
            return
        try:
            self._mgr.create_group(name, description or "")
            self.refresh_groups()
            self.variables_changed.emit()
            QMessageBox.information(parent, "Success", f"Variable group '{name}' created")
        except Exception as exc:
            logger.error("Failed to create group %r: %s", name, exc, exc_info=True)
            QMessageBox.critical(parent, "Error", f"Failed to create group: {exc}")

    def delete_group(self) -> None:
        if not self.current_group_id:
            return
        selected = self.groups_list.selectedItems()
        if not selected:
            return
        group_name = selected[0].text()
        if not confirm_yes_no(
            cast(QWidget, self),
            "Confirm Delete",
            f"Are you sure you want to delete variable group '{group_name}'?\n"
            "This will also delete all variables in the group.",
        ):
            return
        try:
            self._mgr.delete_group(self.current_group_id)
            self.refresh_groups()
            self.variables_changed.emit()
        except Exception as exc:
            logger.error("Failed to delete group %s: %s", self.current_group_id, exc, exc_info=True)
            QMessageBox.critical(cast(QWidget, self), "Error", f"Failed to delete group: {exc}")

    def _show_group_context_menu(self, position: Any) -> None:
        item = self.groups_list.itemAt(position)
        if not item:
            return
        menu = QMenu()
        action_specs = [
            ("rename", "Rename", lambda: self._rename_group(item), False),
            ("delete", "Delete", self.delete_group, True),
        ]
        ordered = self._ordered_context_actions("variables_group", action_specs)
        added_destructive_separator = False
        for action_id, label, callback, is_destructive in ordered:
            if is_destructive and not added_destructive_separator:
                menu.addSeparator()
                added_destructive_separator = True
            menu.addAction(
                label,
                lambda aid=action_id, cb=callback: self._run_context_action(
                    "variables_group", aid, cb,
                ),
            )
        viewport = self.groups_list.viewport()
        if viewport is None:
            return
        menu.exec(viewport.mapToGlobal(position))

    def _rename_group(self, item: QListWidgetItem) -> None:
        group_id = item.data(Qt.ItemDataRole.UserRole)
        old_name = item.text()
        new_name, ok = QInputDialog.getText(
            cast(QWidget, self), "Rename Group", "New name:", text=old_name,
        )
        if not ok or not new_name or new_name == old_name:
            return
        try:
            self._mgr.update_group(group_id, name=new_name)
            self.refresh_groups()
            self.variables_changed.emit()
        except Exception as exc:
            logger.error("Failed to rename group %s: %s", group_id, exc, exc_info=True)
            QMessageBox.critical(self, "Error", f"Failed to rename group: {exc}")

    # ── Variable slots ────────────────────────────────────────────────────────

    def _on_variable_selected(self) -> None:
        has_selection = bool(self.variables_table.selectedItems())
        self.edit_var_btn.setEnabled(has_selection)
        self.remove_var_btn.setEnabled(has_selection)

    def add_variable(self) -> None:
        if not self.current_group_id:
            return
        dialog = VariableDialog(cast(QWidget, self))
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        key, value, description = dialog.get_values()
        if not key:
            QMessageBox.warning(cast(QWidget, self), "Error", "Variable key is required")
            return
        try:
            self._mgr.add_variable(self.current_group_id, key, value, description)
            self.refresh_variables()
            self.variables_changed.emit()
        except Exception as exc:
            logger.error("Failed to add variable %r: %s", key, exc, exc_info=True)
            QMessageBox.critical(cast(QWidget, self), "Error", f"Failed to add variable: {exc}")

    def edit_variable(self) -> None:
        if not self.current_group_id:
            return
        selected_row = self.variables_table.currentRow()
        if selected_row < 0:
            return
        key_item = self.variables_table.item(selected_row, 0)
        value_item = self.variables_table.item(selected_row, 1)
        desc_item = self.variables_table.item(selected_row, 2)
        if key_item is None or value_item is None or desc_item is None:
            return
        key = key_item.text()
        value = value_item.text()
        description = desc_item.text()
        dialog = VariableDialog(cast(QWidget, self), key, value, description)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        new_key, new_value, new_description = dialog.get_values()
        if not new_key:
            QMessageBox.warning(cast(QWidget, self), "Error", "Variable key is required")
            return
        try:
            if new_key != key:
                self._mgr.remove_variable(self.current_group_id, key)
            self._mgr.add_variable(self.current_group_id, new_key, new_value, new_description)
            self.refresh_variables()
            self.variables_changed.emit()
        except Exception as exc:
            logger.error("Failed to update variable %r: %s", key, exc, exc_info=True)
            QMessageBox.critical(cast(QWidget, self), "Error", f"Failed to update variable: {exc}")

    def remove_variable(self) -> None:
        if not self.current_group_id:
            return
        selected_row = self.variables_table.currentRow()
        if selected_row < 0:
            return
        key_item = self.variables_table.item(selected_row, 0)
        if key_item is None:
            return
        key = key_item.text()
        if not confirm_yes_no(
            cast(QWidget, self),
            "Confirm Delete",
            f"Are you sure you want to delete variable '{key}'?",
        ):
            return
        try:
            self._mgr.remove_variable(self.current_group_id, key)
            self.refresh_variables()
            self.variables_changed.emit()
        except Exception as exc:
            logger.error("Failed to remove variable %r: %s", key, exc, exc_info=True)
            QMessageBox.critical(cast(QWidget, self), "Error", f"Failed to remove variable: {exc}")
