"""Dialog for managing collection variables"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from equinox.storage import CollectionManager, Database, VariableGroupManager


def _item_text(table: QTableWidget, row: int, col: int) -> str:
    """Read a cell's text, given the row was populated by this dialog's own refresh."""
    item = table.item(row, col)
    assert item is not None
    return item.text()


class AddVariableGroupDialog(QDialog):
    """Dialog for adding a variable group to collection."""

    def __init__(self, db: Database, collection_id: int, parent: QWidget | None = None):
        super().__init__(parent)
        self._db = db
        self._collection_id = collection_id
        self.setWindowTitle("Add Variable Group")
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Select a variable group:"))

        self.groups_list = QListWidget()
        self.groups_list.itemDoubleClicked.connect(self.accept)
        layout.addWidget(self.groups_list)

        priority_layout = QFormLayout()
        self.priority_spin = QSpinBox()
        self.priority_spin.setRange(-100, 100)
        self.priority_spin.setValue(10)
        self.priority_spin.setToolTip("Lower number = higher priority")
        priority_layout.addRow("Priority:", self.priority_spin)
        layout.addLayout(priority_layout)

        layout.addWidget(QLabel("<i>Lower priority number = higher precedence</i>"))

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
        )
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn:
            ok_btn.setText("Add")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._load_groups()

    def _load_groups(self) -> None:
        """Populate the list with groups not yet assigned to this collection."""
        var_mgr = VariableGroupManager(self._db)
        col_mgr = CollectionManager(self._db)

        assigned_ids = {
            g["id"] for g in col_mgr.list_collection_variable_groups(self._collection_id)
        }

        for group in var_mgr.list_groups():
            if group["id"] in assigned_ids:
                continue
            item = QListWidgetItem(group["name"])
            item.setData(Qt.ItemDataRole.UserRole, group["id"])
            if group["description"]:
                item.setToolTip(group["description"])
            self.groups_list.addItem(item)

    def get_selected_group(self) -> tuple[int, int] | None:
        """Return ``(group_id, priority)``, or ``None`` if nothing selected."""
        selected = self.groups_list.selectedItems()
        if not selected:
            return None
        return selected[0].data(Qt.ItemDataRole.UserRole), self.priority_spin.value()


class CollectionVariablesDialog(QDialog):
    """Dialog for managing collection variables and groups."""

    def __init__(
        self,
        db: Database,
        collection_id: int,
        collection_name: str,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._mgr = CollectionManager(db)
        self._collection_id = collection_id
        self.db = db  # kept for AddVariableGroupDialog

        self.setWindowTitle(f"Variables — {collection_name}")
        self.setMinimumSize(700, 500)

        self._init_ui()
        self.refresh()

    # ── UI construction ───────────────────────────────────────────────────

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        tabs = QTabWidget()

        tabs.addTab(self._build_variables_tab(), "Collection Variables")
        tabs.addTab(self._build_groups_tab(), "Variable Groups")
        tabs.addTab(self._build_all_vars_tab(), "All Variables")

        layout.addWidget(tabs)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

    def _build_variables_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.addWidget(QLabel("<b>Collection-Specific Variables</b>"))
        layout.addWidget(QLabel("<i>These override variables from groups</i>"))

        toolbar = QHBoxLayout()
        self.add_var_btn = QPushButton("Add Variable")
        self.add_var_btn.clicked.connect(self.add_variable)
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
        layout.addLayout(toolbar)

        self.variables_table = QTableWidget()
        self.variables_table.setColumnCount(3)
        self.variables_table.setHorizontalHeaderLabels(["Key", "Value", "Description"])
        hdr = self.variables_table.horizontalHeader()
        assert hdr is not None
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.variables_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.variables_table.itemSelectionChanged.connect(self._on_variable_selected)
        self.variables_table.itemDoubleClicked.connect(self.edit_variable)
        layout.addWidget(self.variables_table)
        return w

    def _build_groups_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.addWidget(QLabel("<b>Variable Groups</b>"))
        layout.addWidget(QLabel("<i>Reusable sets of variables</i>"))

        toolbar = QHBoxLayout()
        self.add_group_btn = QPushButton("Add Group")
        self.add_group_btn.clicked.connect(self.add_group)
        self.remove_group_btn = QPushButton("Remove")
        self.remove_group_btn.clicked.connect(self.remove_group)
        self.remove_group_btn.setEnabled(False)
        toolbar.addWidget(self.add_group_btn)
        toolbar.addWidget(self.remove_group_btn)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        self.groups_table = QTableWidget()
        self.groups_table.setColumnCount(3)
        self.groups_table.setHorizontalHeaderLabels(["Group", "Priority", "Description"])
        hdr = self.groups_table.horizontalHeader()
        assert hdr is not None
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.groups_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.groups_table.itemSelectionChanged.connect(self._on_group_selected)
        layout.addWidget(self.groups_table)
        return w

    def _build_all_vars_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.addWidget(QLabel("<b>All Variables (Merged)</b>"))
        layout.addWidget(QLabel("<i>Final values after precedence resolution</i>"))

        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh_all_variables)
        layout.addWidget(refresh_btn)

        self.all_variables_table = QTableWidget()
        self.all_variables_table.setColumnCount(2)
        self.all_variables_table.setHorizontalHeaderLabels(["Key", "Value"])
        hdr = self.all_variables_table.horizontalHeader()
        assert hdr is not None
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.all_variables_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.all_variables_table)
        return w

    # ── Refresh helpers ───────────────────────────────────────────────────

    def refresh(self) -> None:
        """Refresh all three tables."""
        self.refresh_variables()
        self.refresh_groups()
        self.refresh_all_variables()

    def refresh_variables(self) -> None:
        """Reload the collection-variables table."""
        variables = self._mgr.list_collection_variables(self._collection_id)
        table = self.variables_table
        table.setUpdatesEnabled(False)
        try:
            table.setRowCount(len(variables))
            for row, var in enumerate(variables):
                table.setItem(row, 0, QTableWidgetItem(var["key"]))
                table.setItem(row, 1, QTableWidgetItem(var["value"]))
                table.setItem(row, 2, QTableWidgetItem(var["description"] or ""))
        finally:
            table.setUpdatesEnabled(True)

    def refresh_groups(self) -> None:
        """Reload the variable-groups table."""
        groups = self._mgr.list_collection_variable_groups(self._collection_id)
        table = self.groups_table
        table.setUpdatesEnabled(False)
        try:
            table.setRowCount(len(groups))
            for row, group in enumerate(groups):
                name_item = QTableWidgetItem(group["name"])
                name_item.setData(Qt.ItemDataRole.UserRole, group["id"])
                table.setItem(row, 0, name_item)
                table.setItem(row, 1, QTableWidgetItem(str(group["priority"])))
                table.setItem(row, 2, QTableWidgetItem(group["description"] or ""))
        finally:
            table.setUpdatesEnabled(True)

    def refresh_all_variables(self) -> None:
        """Reload the merged all-variables view."""
        all_vars = self._mgr.get_all_collection_variables(self._collection_id)
        table = self.all_variables_table
        table.setUpdatesEnabled(False)
        try:
            table.setRowCount(len(all_vars))
            for row, (key, value) in enumerate(sorted(all_vars.items())):
                table.setItem(row, 0, QTableWidgetItem(key))
                table.setItem(row, 1, QTableWidgetItem(value))
        finally:
            table.setUpdatesEnabled(True)

    # ── Selection handlers ────────────────────────────────────────────────

    def _on_variable_selected(self) -> None:
        has = self.variables_table.currentRow() >= 0
        self.edit_var_btn.setEnabled(has)
        self.remove_var_btn.setEnabled(has)

    def _on_group_selected(self) -> None:
        self.remove_group_btn.setEnabled(self.groups_table.currentRow() >= 0)

    # ── CRUD actions ──────────────────────────────────────────────────────

    def add_variable(self) -> None:
        # Deferred to avoid circular import (variables_panel imports from dialogs).
        from equinox.gui.variables_panel import VariableDialog

        dialog = VariableDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        key, value, description = dialog.get_values()
        if not key:
            QMessageBox.warning(self, "Error", "Variable key is required")
            return
        try:
            self._mgr.add_variable(self._collection_id, key, value, description)
            self.refresh()
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to add variable: {exc}")

    def edit_variable(self) -> None:
        row = self.variables_table.currentRow()
        if row < 0:
            return

        key = _item_text(self.variables_table, row, 0)
        value = _item_text(self.variables_table, row, 1)
        description = _item_text(self.variables_table, row, 2)

        # Deferred to avoid circular import (variables_panel imports from dialogs).
        from equinox.gui.variables_panel import VariableDialog

        dialog = VariableDialog(self, key, value, description)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        new_key, new_value, new_description = dialog.get_values()
        if not new_key:
            QMessageBox.warning(self, "Error", "Variable key is required")
            return
        try:
            if new_key != key:
                self._mgr.remove_variable(self._collection_id, key)
            self._mgr.add_variable(self._collection_id, new_key, new_value, new_description)
            self.refresh()
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to update variable: {exc}")

    def remove_variable(self) -> None:
        row = self.variables_table.currentRow()
        if row < 0:
            return
        key = _item_text(self.variables_table, row, 0)
        reply = QMessageBox.question(
            self,
            "Confirm Delete",
            f"Delete variable '{key}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            self._mgr.remove_variable(self._collection_id, key)
            self.refresh()
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to remove variable: {exc}")

    def add_group(self) -> None:
        dialog = AddVariableGroupDialog(self.db, self._collection_id, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        selection = dialog.get_selected_group()
        if selection is None:
            QMessageBox.warning(self, "Error", "Please select a group")
            return
        group_id, priority = selection
        try:
            self._mgr.add_variable_group(self._collection_id, group_id, priority)
            self.refresh()
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to add group: {exc}")

    def remove_group(self) -> None:
        row = self.groups_table.currentRow()
        if row < 0:
            return
        group_item = self.groups_table.item(row, 0)
        assert group_item is not None
        group_name = group_item.text()
        group_id = group_item.data(Qt.ItemDataRole.UserRole)
        reply = QMessageBox.question(
            self,
            "Confirm Remove",
            f"Remove group '{group_name}' from this collection?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            self._mgr.remove_variable_group(self._collection_id, group_id)
            self.refresh()
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to remove group: {exc}")
