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
)
from PyQt6.QtCore import Qt, pyqtSignal, QSettings

from equinox.storage import Database, VariableGroupManager


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
    """Panel for managing variable groups"""

    variables_changed = pyqtSignal()

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db = db
        self.current_group_id = None
        self._settings = QSettings("Equinox", "Equinox")
        self._init_ui()
        self.refresh_groups()

    def _init_ui(self):
        """Initialize UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

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
