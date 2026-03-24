"""Variable groups management panel"""

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QSplitter,
    QListWidget,
    QListWidgetItem,
    QTableWidget,
    QTableWidgetItem,
    QPushButton,
    QLabel,
    QInputDialog,
    QMessageBox,
    QHeaderView,
    QMenu,
    QDialog,
    QLineEdit,
    QTextEdit,
    QFormLayout,
    QDialogButtonBox,
    QGroupBox,
    QApplication,
)
from PyQt6.QtCore import Qt, pyqtSignal, QSettings


import os
import re

from equinox.storage import Database, VariableGroupManager, EnvironmentManager
from equinox.core.interpolation import VariableInterpolator


class VariableDialog(QDialog):
    """Dialog for adding/editing a variable"""

    def __init__(self, parent=None, key="", value="", description=""):
        super().__init__(parent)
        self.setWindowTitle("Variable")
        self.setMinimumWidth(500)

        layout = QFormLayout(self)

        self.key_input = QLineEdit(key)
        self.key_input.setPlaceholderText("e.g., API_URL")
        layout.addRow("Key:", self.key_input)

        self.value_input = QLineEdit(value)
        self.value_input.setPlaceholderText("e.g., https://api.example.com")
        layout.addRow("Value:", self.value_input)

        self.description_input = QTextEdit(description)
        self.description_input.setPlaceholderText("Optional description")
        self.description_input.setMaximumHeight(80)
        layout.addRow("Description:", self.description_input)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def get_values(self):
        """Get dialog values"""
        return (
            self.key_input.text().strip(),
            self.value_input.text(),
            self.description_input.toPlainText().strip()
        )


class VariablesPanel(QWidget):
    """Panel for managing variable groups and viewing captured session variables."""

    variables_changed = pyqtSignal()
    clear_session_requested = pyqtSignal()

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db = db
        self.current_group_id = None
        self._settings = QSettings("Equinox", "Equinox")
        self._session_var_count = 0
        self._init_ui()
        self.refresh_groups()

    def _init_ui(self):
        """Initialize UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # ── Session Variables (captured at runtime) ───────────────────
        self._session_group = QGroupBox("Session Variables")
        self._session_group.setCheckable(True)
        self._session_group.setChecked(False)
        session_layout = QVBoxLayout(self._session_group)
        session_layout.setContentsMargins(4, 4, 4, 4)
        session_layout.setSpacing(4)

        session_header = QHBoxLayout()
        self._session_count_label = QLabel("No captured variables")
        self._session_count_label.setObjectName("mutedLabel")
        session_header.addWidget(self._session_count_label)
        session_header.addStretch()
        self._session_copy_btn = QPushButton("Copy All")
        self._session_copy_btn.setToolTip("Copy all session variables to clipboard as KEY=VALUE lines")
        self._session_copy_btn.clicked.connect(self._copy_session_vars)
        self._session_copy_btn.setEnabled(False)
        session_header.addWidget(self._session_copy_btn)
        self._session_delete_btn = QPushButton("Delete")
        self._session_delete_btn.setToolTip("Delete selected session variable")
        self._session_delete_btn.clicked.connect(self._delete_session_var)
        self._session_delete_btn.setEnabled(False)
        session_header.addWidget(self._session_delete_btn)
        self._session_clear_btn = QPushButton("Clear All")
        self._session_clear_btn.setToolTip("Remove all captured session variables")
        self._session_clear_btn.clicked.connect(self._on_clear_session)
        self._session_clear_btn.setEnabled(False)
        session_header.addWidget(self._session_clear_btn)
        session_layout.addLayout(session_header)

        self._session_table = QTableWidget()
        self._session_table.setColumnCount(2)
        self._session_table.setHorizontalHeaderLabels(["Variable", "Value"])
        self._session_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self._session_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self._session_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self._session_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        self._session_table.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self._session_table.customContextMenuRequested.connect(
            self._show_session_context_menu
        )
        self._session_table.itemSelectionChanged.connect(self._on_session_selection)
        self._session_table.setMaximumHeight(200)
        session_layout.addWidget(self._session_table)

        layout.addWidget(self._session_group)

        # ── Variable Groups (persisted in DB) ─────────────────────────
        # Splitter for groups list and variables table
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left side - Groups list
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)

        groups_label = QLabel("<b>Groups</b>")
        left_layout.addWidget(groups_label)

        # Groups toolbar
        groups_toolbar = QHBoxLayout()
        self.new_group_btn = QPushButton("New Group")
        self.new_group_btn.clicked.connect(self.create_group)
        self.delete_group_btn = QPushButton("Delete Group")
        self.delete_group_btn.clicked.connect(self.delete_group)
        self.delete_group_btn.setEnabled(False)
        groups_toolbar.addWidget(self.new_group_btn)
        groups_toolbar.addWidget(self.delete_group_btn)
        groups_toolbar.addStretch()
        left_layout.addLayout(groups_toolbar)

        # Groups list
        self.groups_list = QListWidget()
        self.groups_list.itemSelectionChanged.connect(self._on_group_selected)
        self.groups_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.groups_list.customContextMenuRequested.connect(self._show_group_context_menu)
        left_layout.addWidget(self.groups_list)

        splitter.addWidget(left_widget)

        # Right side - Variables table
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)

        variables_label = QLabel("<b>Variables</b>")
        right_layout.addWidget(variables_label)

        # Variables toolbar
        vars_toolbar = QHBoxLayout()
        self.add_var_btn = QPushButton("Add Variable")
        self.add_var_btn.clicked.connect(self.add_variable)
        self.add_var_btn.setEnabled(False)
        self.edit_var_btn = QPushButton("Edit")
        self.edit_var_btn.clicked.connect(self.edit_variable)
        self.edit_var_btn.setEnabled(False)
        self.remove_var_btn = QPushButton("Remove")
        self.remove_var_btn.clicked.connect(self.remove_variable)
        self.remove_var_btn.setEnabled(False)
        vars_toolbar.addWidget(self.add_var_btn)
        vars_toolbar.addWidget(self.edit_var_btn)
        vars_toolbar.addWidget(self.remove_var_btn)
        vars_toolbar.addStretch()
        right_layout.addLayout(vars_toolbar)

        # Variables table
        self.variables_table = QTableWidget()
        self.variables_table.setColumnCount(3)
        self.variables_table.setHorizontalHeaderLabels(["Key", "Value", "Description"])
        self.variables_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.variables_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.variables_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.variables_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.variables_table.itemSelectionChanged.connect(self._on_variable_selected)
        self.variables_table.itemDoubleClicked.connect(self.edit_variable)
        right_layout.addWidget(self.variables_table)

        splitter.addWidget(right_widget)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(5)

        # Restore saved splitter position (#1)
        saved = self._settings.value("splitter/variables")
        if saved:
            try:
                splitter.setSizes([int(x) for x in saved])
            except Exception:
                splitter.setSizes([250, 550])
        else:
            splitter.setSizes([250, 550])
        splitter.splitterMoved.connect(
            lambda: self._settings.setValue("splitter/variables", splitter.sizes())
        )

        layout.addWidget(splitter)

    def refresh(self):
        """Public refresh method — reloads groups and current variables."""
        self.refresh_groups()
        if self.current_group_id:
            self.refresh_variables()

    def refresh_groups(self):
        """Refresh groups list"""
        self.groups_list.clear()
        self.current_group_id = None

        mgr = VariableGroupManager(self.db)
        groups = mgr.list_groups()

        for group in groups:
            item = QListWidgetItem(group["name"])
            item.setData(Qt.ItemDataRole.UserRole, group["id"])
            if group["description"]:
                item.setToolTip(group["description"])
            self.groups_list.addItem(item)

    def _on_group_selected(self):
        """Handle group selection"""
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

    def refresh_variables(self):
        """Refresh variables for selected group"""
        if not self.current_group_id:
            self.variables_table.setRowCount(0)
            return

        mgr = VariableGroupManager(self.db)
        variables = mgr.list_group_variables(self.current_group_id)

        self.variables_table.setRowCount(len(variables))
        for row, var in enumerate(variables):
            key_item = QTableWidgetItem(var["key"])
            key_item.setData(Qt.ItemDataRole.UserRole, var["id"])
            value_item = QTableWidgetItem(var["value"])
            desc_item = QTableWidgetItem(var["description"] or "")

            self.variables_table.setItem(row, 0, key_item)
            self.variables_table.setItem(row, 1, value_item)
            self.variables_table.setItem(row, 2, desc_item)

            # Tooltip: show raw value and the interpolated result using the
            # currently active environment. This gives users a quick preview
            # of what {{vars}} will resolve to in practice when hovered.
            try:
                # Build an effective variables map mirroring the request-time
                # resolution precedence: active environment -> OS env -> session
                # vars -> path params (path params override prior values).
                variables = {}

                # Active environment variables
                try:
                    env_mgr = EnvironmentManager(self.db)
                    active = env_mgr.get_active_environment()
                    if active:
                        variables.update(active.get("variables", {}))
                except Exception:
                    pass

                # Filter OS environment to variable-like names
                try:
                    valid_var_pattern = re.compile(r'^[a-zA-Z0-9_-]+$')
                    os_env_filtered = {
                        k: v for k, v in os.environ.items()
                        if isinstance(v, str) and valid_var_pattern.match(k)
                    }
                    if os_env_filtered:
                        variables.update(os_env_filtered)
                except Exception:
                    pass

                # Session vars and path params from RequestPanel (override env)
                try:
                    win = self.window()
                    rp = getattr(win, "request_panel", None)
                    if rp is not None:
                        try:
                            session_vars = rp.get_session_vars()
                            variables.update(session_vars)
                        except Exception:
                            pass

                        try:
                            if getattr(rp, 'path_params_table', None) is not None:
                                path_params = rp.path_params_table.get_all_data()
                                if path_params:
                                    variables.update(path_params)
                        except Exception:
                            pass
                except Exception:
                    pass

                raw = var.get("value") or ""
                if raw:
                    try:
                        interpolated = VariableInterpolator.interpolate(raw, variables)
                    except Exception:
                        # Fall back to active-env-only interpolation for robustness
                        try:
                            env_mgr = EnvironmentManager(self.db)
                            interpolated = env_mgr.interpolate_variables(raw)
                        except Exception:
                            interpolated = raw
                else:
                    interpolated = ""

                if interpolated != raw:
                    tip = f"Raw: {raw}\nInterpolated: {interpolated}"
                else:
                    tip = raw

                # Attach tooltip to the value cell (hovering the value shows resolved text)
                value_item.setToolTip(tip)
                # Also attach a helpful tooltip to the key so hovering the name shows resolved value
                key_item.setToolTip(f"{var['key']} → {interpolated}")
            except Exception:
                # Non-fatal: if interpolation fails, leave default tooltips
                pass

    def _on_variable_selected(self):
        """Handle variable selection"""
        selected = self.variables_table.selectedItems()
        has_selection = len(selected) > 0
        self.edit_var_btn.setEnabled(has_selection)
        self.remove_var_btn.setEnabled(has_selection)

    def create_group(self):
        """Create a new variable group"""
        name, ok = QInputDialog.getText(self, "New Variable Group", "Group name:")
        if not ok or not name:
            return

        description, ok = QInputDialog.getText(self, "New Variable Group", "Description (optional):")
        if not ok:
            return

        mgr = VariableGroupManager(self.db)
        try:
            mgr.create_group(name, description or "")
            self.refresh_groups()
            self.variables_changed.emit()
            QMessageBox.information(self, "Success", f"Variable group '{name}' created")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to create group: {e}")

    def delete_group(self):
        """Delete selected variable group"""
        if not self.current_group_id:
            return

        selected = self.groups_list.selectedItems()
        if not selected:
            return

        group_name = selected[0].text()

        reply = QMessageBox.question(
            self,
            "Confirm Delete",
            f"Are you sure you want to delete variable group '{group_name}'?\n"
            "This will also delete all variables in the group.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            mgr = VariableGroupManager(self.db)
            try:
                mgr.delete_group(self.current_group_id)
                self.refresh_groups()
                self.variables_changed.emit()
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to delete group: {e}")

    def add_variable(self):
        """Add a new variable to the selected group"""
        if not self.current_group_id:
            return

        dialog = VariableDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            key, value, description = dialog.get_values()

            if not key:
                QMessageBox.warning(self, "Error", "Variable key is required")
                return

            mgr = VariableGroupManager(self.db)
            try:
                mgr.add_variable(self.current_group_id, key, value, description)
                self.refresh_variables()
                self.variables_changed.emit()
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to add variable: {e}")

    def edit_variable(self):
        """Edit selected variable"""
        if not self.current_group_id:
            return

        selected_row = self.variables_table.currentRow()
        if selected_row < 0:
            return

        key = self.variables_table.item(selected_row, 0).text()
        value = self.variables_table.item(selected_row, 1).text()
        description = self.variables_table.item(selected_row, 2).text()

        dialog = VariableDialog(self, key, value, description)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_key, new_value, new_description = dialog.get_values()

            if not new_key:
                QMessageBox.warning(self, "Error", "Variable key is required")
                return

            mgr = VariableGroupManager(self.db)
            try:
                # If key changed, remove old and add new
                if new_key != key:
                    mgr.remove_variable(self.current_group_id, key)

                mgr.add_variable(self.current_group_id, new_key, new_value, new_description)
                self.refresh_variables()
                self.variables_changed.emit()
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to update variable: {e}")

    def remove_variable(self):
        """Remove selected variable"""
        if not self.current_group_id:
            return

        selected_row = self.variables_table.currentRow()
        if selected_row < 0:
            return

        key = self.variables_table.item(selected_row, 0).text()

        reply = QMessageBox.question(
            self,
            "Confirm Delete",
            f"Are you sure you want to delete variable '{key}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            mgr = VariableGroupManager(self.db)
            try:
                mgr.remove_variable(self.current_group_id, key)
                self.refresh_variables()
                self.variables_changed.emit()
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to remove variable: {e}")

    def _show_group_context_menu(self, position):
        """Show context menu for groups"""
        item = self.groups_list.itemAt(position)
        if not item:
            return

        menu = QMenu()
        rename_action = menu.addAction("Rename")
        delete_action = menu.addAction("Delete")

        action = menu.exec(self.groups_list.viewport().mapToGlobal(position))

        if action == rename_action:
            self._rename_group(item)
        elif action == delete_action:
            self.delete_group()

    def _rename_group(self, item):
        """Rename a group"""
        group_id = item.data(Qt.ItemDataRole.UserRole)
        old_name = item.text()

        new_name, ok = QInputDialog.getText(self, "Rename Group", "New name:", text=old_name)
        if not ok or not new_name or new_name == old_name:
            return

        mgr = VariableGroupManager(self.db)
        try:
            mgr.update_group(group_id, name=new_name)
            self.refresh_groups()
            self.variables_changed.emit()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to rename group: {e}")

    # ── Session Variables ──────────────────────────────────────────────

    def refresh_session_vars(self, session_vars: dict) -> None:
        """Repopulate the session variables table.

        Called by the signal from ``RequestPanel.session_vars_changed``.
        """
        self._session_var_count = len(session_vars)
        self._session_table.setRowCount(self._session_var_count)

        for row, (key, value) in enumerate(sorted(session_vars.items())):
            key_item = QTableWidgetItem(key)
            key_item.setFlags(key_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            val_item = QTableWidgetItem(str(value))
            val_item.setFlags(val_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._session_table.setItem(row, 0, key_item)
            self._session_table.setItem(row, 1, val_item)

        has_vars = self._session_var_count > 0
        self._session_clear_btn.setEnabled(has_vars)
        self._session_copy_btn.setEnabled(has_vars)
        self._session_delete_btn.setEnabled(False)
        noun = "variable" if self._session_var_count == 1 else "variables"
        self._session_count_label.setText(
            f"{self._session_var_count} captured {noun}"
            if has_vars else "No captured variables"
        )
        # Auto-expand when first variable arrives, stay collapsed when empty
        if has_vars and not self._session_group.isChecked():
            self._session_group.setChecked(True)
        self._update_tab_badge()

    def _on_session_selection(self) -> None:
        """Enable/disable delete button based on session table selection."""
        has_sel = len(self._session_table.selectedItems()) > 0
        self._session_delete_btn.setEnabled(has_sel)

    def _on_clear_session(self) -> None:
        """Handle Clear All button click."""
        if self._session_var_count == 0:
            return
        self.clear_session_requested.emit()

    def _delete_session_var(self) -> None:
        """Delete the selected session variable and notify."""
        row = self._session_table.currentRow()
        if row < 0:
            return
        key_item = self._session_table.item(row, 0)
        if not key_item:
            return
        key = key_item.text()
        # Ask the request panel to remove this variable
        try:
            win = self.window()
            rp = getattr(win, "request_panel", None)
            if rp and key in rp._session_vars:
                del rp._session_vars[key]
                rp.session_vars_changed.emit(dict(rp._session_vars))
        except Exception:
            pass

    def _copy_session_vars(self) -> None:
        """Copy all session variables to clipboard as KEY=VALUE lines."""
        lines = []
        for row in range(self._session_table.rowCount()):
            key_item = self._session_table.item(row, 0)
            val_item = self._session_table.item(row, 1)
            if key_item and val_item:
                lines.append(f"{key_item.text()}={val_item.text()}")
        if lines:
            clipboard = QApplication.clipboard()
            if clipboard:
                clipboard.setText("\n".join(lines))

    def _show_session_context_menu(self, position) -> None:
        """Context menu for session variables table (copy value / delete)."""
        item = self._session_table.itemAt(position)
        if not item:
            return
        row = item.row()
        menu = QMenu()
        copy_key_action = menu.addAction("Copy Variable Name")
        copy_val_action = menu.addAction("Copy Value")
        menu.addSeparator()
        delete_action = menu.addAction("Delete")

        action = menu.exec(self._session_table.viewport().mapToGlobal(position))
        clipboard = QApplication.clipboard()
        if action == copy_key_action:
            key_item = self._session_table.item(row, 0)
            if key_item and clipboard:
                clipboard.setText(key_item.text())
        elif action == copy_val_action:
            val_item = self._session_table.item(row, 1)
            if val_item and clipboard:
                clipboard.setText(val_item.text())
        elif action == delete_action:
            self._session_table.setCurrentItem(self._session_table.item(row, 0))
            self._delete_session_var()

    def _update_tab_badge(self) -> None:
        """Update the Variables tab title to show a session var count badge."""
        try:
            tab_widget = self.parent()
            if tab_widget is None:
                return
            # Walk up to find the QTabWidget that contains this panel
            from PyQt6.QtWidgets import QTabWidget
            while tab_widget and not isinstance(tab_widget, QTabWidget):
                tab_widget = tab_widget.parent()
            if not isinstance(tab_widget, QTabWidget):
                return
            idx = tab_widget.indexOf(self)
            if idx < 0:
                return
            if self._session_var_count > 0:
                tab_widget.setTabText(idx, f"Variables ({self._session_var_count})")
            else:
                tab_widget.setTabText(idx, "Variables")
        except Exception:
            pass

