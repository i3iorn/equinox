"""Dialog for managing collection variables"""

from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QTabWidget,
    QWidget,
    QTableWidget,
    QTableWidgetItem,
    QPushButton,
    QLabel,
    QInputDialog,
    QMessageBox,
    QHeaderView,
    QListWidget,
    QListWidgetItem,
    QLineEdit,
    QTextEdit,
    QFormLayout,
    QSpinBox,
    QDialogButtonBox,
)
from PyQt6.QtCore import Qt

from equinox.storage import Database, CollectionManager, VariableGroupManager


class AddVariableGroupDialog(QDialog):
    """Dialog for adding a variable group to collection"""

    def __init__(self, db: Database, collection_id: int, parent=None):
        super().__init__(parent)
        self.db = db
        self.collection_id = collection_id
        self.setWindowTitle("Add Variable Group")
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)

        # Group selection
        layout.addWidget(QLabel("Select a variable group:"))

        self.groups_list = QListWidget()
        self.groups_list.itemDoubleClicked.connect(self.accept)
        layout.addWidget(self.groups_list)

        # Priority
        priority_layout = QFormLayout()
        self.priority_spin = QSpinBox()
        self.priority_spin.setRange(-100, 100)
        self.priority_spin.setValue(10)
        self.priority_spin.setToolTip("Lower number = higher priority")
        priority_layout.addRow("Priority:", self.priority_spin)
        layout.addLayout(priority_layout)

        layout.addWidget(QLabel("<i>Lower priority number = higher precedence</i>"))

        # Buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Add | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._load_groups()

    def _load_groups(self):
        """Load available groups"""
        var_mgr = VariableGroupManager(self.db)
        col_mgr = CollectionManager(self.db)

        # Get all groups
        all_groups = var_mgr.list_groups()

        # Get groups already in collection
        assigned_groups = col_mgr.list_collection_variable_groups(self.collection_id)
        assigned_ids = {g["id"] for g in assigned_groups}

        # Show only unassigned groups
        for group in all_groups:
            if group["id"] not in assigned_ids:
                item = QListWidgetItem(group["name"])
                item.setData(Qt.ItemDataRole.UserRole, group["id"])
                if group["description"]:
                    item.setToolTip(group["description"])
                self.groups_list.addItem(item)

    def get_selected_group(self):
        """Get selected group ID and priority"""
        selected = self.groups_list.selectedItems()
        if not selected:
            return None, None

        group_id = selected[0].data(Qt.ItemDataRole.UserRole)
        priority = self.priority_spin.value()
        return group_id, priority


class CollectionVariablesDialog(QDialog):
    """Dialog for managing collection variables and groups"""

    def __init__(self, db: Database, collection_id: int, collection_name: str, parent=None):
        super().__init__(parent)
        self.db = db
        self.collection_id = collection_id
        self.setWindowTitle(f"Variables - {collection_name}")
        self.setMinimumSize(700, 500)

        self._init_ui()
        self.refresh()

    def _init_ui(self):
        """Initialize UI"""
        layout = QVBoxLayout(self)

        tabs = QTabWidget()

        # Collection Variables tab
        vars_tab = QWidget()
        vars_layout = QVBoxLayout(vars_tab)

        vars_layout.addWidget(QLabel("<b>Collection-Specific Variables</b>"))
        vars_layout.addWidget(QLabel("<i>These override variables from groups</i>"))

        # Variables toolbar
        vars_toolbar = QHBoxLayout()
        self.add_var_btn = QPushButton("Add Variable")
        self.add_var_btn.clicked.connect(self.add_variable)
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
        vars_layout.addLayout(vars_toolbar)

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
        vars_layout.addWidget(self.variables_table)

        tabs.addTab(vars_tab, "Collection Variables")

        # Variable Groups tab
        groups_tab = QWidget()
        groups_layout = QVBoxLayout(groups_tab)

        groups_layout.addWidget(QLabel("<b>Variable Groups</b>"))
        groups_layout.addWidget(QLabel("<i>Reusable sets of variables</i>"))

        # Groups toolbar
        groups_toolbar = QHBoxLayout()
        self.add_group_btn = QPushButton("Add Group")
        self.add_group_btn.clicked.connect(self.add_group)
        self.remove_group_btn = QPushButton("Remove")
        self.remove_group_btn.clicked.connect(self.remove_group)
        self.remove_group_btn.setEnabled(False)
        groups_toolbar.addWidget(self.add_group_btn)
        groups_toolbar.addWidget(self.remove_group_btn)
        groups_toolbar.addStretch()
        groups_layout.addLayout(groups_toolbar)

        # Groups table
        self.groups_table = QTableWidget()
        self.groups_table.setColumnCount(3)
        self.groups_table.setHorizontalHeaderLabels(["Group", "Priority", "Description"])
        self.groups_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.groups_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.groups_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.groups_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.groups_table.itemSelectionChanged.connect(self._on_group_selected)
        groups_layout.addWidget(self.groups_table)

        tabs.addTab(groups_tab, "Variable Groups")

        # All Variables (read-only) tab
        all_vars_tab = QWidget()
        all_vars_layout = QVBoxLayout(all_vars_tab)

        all_vars_layout.addWidget(QLabel("<b>All Variables (Merged)</b>"))
        all_vars_layout.addWidget(QLabel("<i>Final values after precedence resolution</i>"))

        refresh_all_btn = QPushButton("Refresh")
        refresh_all_btn.clicked.connect(self.refresh_all_variables)
        all_vars_layout.addWidget(refresh_all_btn)

        self.all_variables_table = QTableWidget()
        self.all_variables_table.setColumnCount(2)
        self.all_variables_table.setHorizontalHeaderLabels(["Key", "Value"])
        self.all_variables_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.all_variables_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.all_variables_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        all_vars_layout.addWidget(self.all_variables_table)

        tabs.addTab(all_vars_tab, "All Variables")

        layout.addWidget(tabs)

        # Close button
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

    def refresh(self):
        """Refresh all tables"""
        self.refresh_variables()
        self.refresh_groups()
        self.refresh_all_variables()

    def refresh_variables(self):
        """Refresh collection variables"""
        mgr = CollectionManager(self.db)
        variables = mgr.list_collection_variables(self.collection_id)

        self.variables_table.setRowCount(len(variables))
        for row, var in enumerate(variables):
            self.variables_table.setItem(row, 0, QTableWidgetItem(var["key"]))
            self.variables_table.setItem(row, 1, QTableWidgetItem(var["value"]))
            self.variables_table.setItem(row, 2, QTableWidgetItem(var["description"] or ""))

    def refresh_groups(self):
        """Refresh variable groups"""
        mgr = CollectionManager(self.db)
        groups = mgr.list_collection_variable_groups(self.collection_id)

        self.groups_table.setRowCount(len(groups))
        for row, group in enumerate(groups):
            name_item = QTableWidgetItem(group["name"])
            name_item.setData(Qt.ItemDataRole.UserRole, group["id"])
            priority_item = QTableWidgetItem(str(group["priority"]))
            desc_item = QTableWidgetItem(group["description"] or "")

            self.groups_table.setItem(row, 0, name_item)
            self.groups_table.setItem(row, 1, priority_item)
            self.groups_table.setItem(row, 2, desc_item)

    def refresh_all_variables(self):
        """Refresh merged variables view"""
        mgr = CollectionManager(self.db)
        all_vars = mgr.get_all_collection_variables(self.collection_id)

        self.all_variables_table.setRowCount(len(all_vars))
        for row, (key, value) in enumerate(sorted(all_vars.items())):
            self.all_variables_table.setItem(row, 0, QTableWidgetItem(key))
            self.all_variables_table.setItem(row, 1, QTableWidgetItem(value))

    def _on_variable_selected(self):
        """Handle variable selection"""
        has_selection = len(self.variables_table.selectedItems()) > 0
        self.edit_var_btn.setEnabled(has_selection)
        self.remove_var_btn.setEnabled(has_selection)

    def _on_group_selected(self):
        """Handle group selection"""
        has_selection = len(self.groups_table.selectedItems()) > 0
        self.remove_group_btn.setEnabled(has_selection)

    def add_variable(self):
        """Add a new variable"""
        from equinox.gui.variables_panel import VariableDialog

        dialog = VariableDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            key, value, description = dialog.get_values()

            if not key:
                QMessageBox.warning(self, "Error", "Variable key is required")
                return

            mgr = CollectionManager(self.db)
            try:
                mgr.add_variable(self.collection_id, key, value, description)
                self.refresh()
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to add variable: {e}")

    def edit_variable(self):
        """Edit selected variable"""
        selected_row = self.variables_table.currentRow()
        if selected_row < 0:
            return

        key = self.variables_table.item(selected_row, 0).text()
        value = self.variables_table.item(selected_row, 1).text()
        description = self.variables_table.item(selected_row, 2).text()

        from equinox.gui.variables_panel import VariableDialog

        dialog = VariableDialog(self, key, value, description)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_key, new_value, new_description = dialog.get_values()

            if not new_key:
                QMessageBox.warning(self, "Error", "Variable key is required")
                return

            mgr = CollectionManager(self.db)
            try:
                # If key changed, remove old
                if new_key != key:
                    mgr.remove_variable(self.collection_id, key)

                mgr.add_variable(self.collection_id, new_key, new_value, new_description)
                self.refresh()
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to update variable: {e}")

    def remove_variable(self):
        """Remove selected variable"""
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
            mgr = CollectionManager(self.db)
            try:
                mgr.remove_variable(self.collection_id, key)
                self.refresh()
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to remove variable: {e}")

    def add_group(self):
        """Add a variable group to collection"""
        dialog = AddVariableGroupDialog(self.db, self.collection_id, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            group_id, priority = dialog.get_selected_group()

            if group_id is None:
                QMessageBox.warning(self, "Error", "Please select a group")
                return

            mgr = CollectionManager(self.db)
            try:
                mgr.add_variable_group(self.collection_id, group_id, priority)
                self.refresh()
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to add group: {e}")

    def remove_group(self):
        """Remove selected variable group"""
        selected_row = self.groups_table.currentRow()
        if selected_row < 0:
            return

        group_name = self.groups_table.item(selected_row, 0).text()
        group_id = self.groups_table.item(selected_row, 0).data(Qt.ItemDataRole.UserRole)

        reply = QMessageBox.question(
            self,
            "Confirm Remove",
            f"Remove variable group '{group_name}' from this collection?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            mgr = CollectionManager(self.db)
            try:
                mgr.remove_variable_group(self.collection_id, group_id)
                self.refresh()
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to remove group: {e}")
