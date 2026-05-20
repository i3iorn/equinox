"""Variable groups management panel"""

from __future__ import annotations

import logging

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from equinox.core.exceptions import ValidationError
from equinox.core.interpolation import (
    VariableInterpolator,
    collect_interpolation_variables_detailed,
)
from equinox.core.validation import Validator
from equinox.storage import (
    Database,
    EnvironmentManager,
    GlobalVariablesManager,
    VariableGroupManager,
)

from .ui_common import (
    configure_splitter_persistence,
    confirm_yes_no,
    create_muted_label,
    create_panel_layout,
    get_gui_settings,
)

__all__ = ["VariablesPanel", "VariableDialog"]

logger = logging.getLogger(__name__)

_GLOBAL_TABLE_MAX_VISIBLE_ROWS = 3
_GLOBAL_TABLE_MIN_VISIBLE_ROWS = 1
_SESSION_TABLE_MAX_VISIBLE_ROWS = 4
_SESSION_TABLE_MIN_VISIBLE_ROWS = 1


# ── Variable edit dialog ──────────────────────────────────────────────────────


class VariableDialog(QDialog):
    """Dialog for adding/editing a variable."""

    def __init__(
        self,
        parent: QWidget | None = None,
        key: str = "",
        value: str = "",
        description: str = "",
    ) -> None:
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

    def get_values(self) -> tuple[str, str, str]:
        return (
            self.key_input.text().strip(),
            self.value_input.text(),
            self.description_input.toPlainText().strip(),
        )


# ── Main panel ────────────────────────────────────────────────────────────────


class VariablesPanel(QWidget):
    """Panel for managing variable groups and viewing captured session variables."""

    variables_changed = pyqtSignal()
    clear_session_requested = pyqtSignal()

    def __init__(self, db: Database, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.db = db
        # Cache managers — lightweight DB wrappers, safe to reuse across calls.
        self._mgr = VariableGroupManager(db)
        self._global_mgr = GlobalVariablesManager(db)
        self._env_mgr = EnvironmentManager(db)
        self.current_group_id: int | None = None
        self._settings = get_gui_settings()
        self._global_var_count = 0
        self._session_var_count = 0
        self._init_ui()
        self.refresh_global_vars()
        self.refresh_groups()

    # ── UI construction ───────────────────────────────────────────────────────

    def _init_ui(self) -> None:
        layout = create_panel_layout(self)

        # ── Global Variables (persisted app-wide) ─────────────────────
        self._global_group = QGroupBox("Global Variables")
        self._global_group.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
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
        self._global_table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        global_layout.addWidget(self._global_table)

        layout.addWidget(self._global_group)

        # ── Session Variables (captured at runtime) ───────────────────
        self._session_group = QGroupBox("Session Variables")
        self._session_group.setCheckable(True)
        self._session_group.setChecked(True)
        self._session_group.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        session_layout = QVBoxLayout(self._session_group)
        session_layout.setContentsMargins(4, 4, 4, 4)
        session_layout.setSpacing(4)

        session_header = QHBoxLayout()
        self._session_count_label = create_muted_label("No captured variables")
        session_header.addWidget(self._session_count_label)
        session_header.addStretch()
        self._session_copy_btn = QPushButton("Copy All")
        self._session_copy_btn.setToolTip(
            "Copy all session variables to clipboard as KEY=VALUE lines"
        )
        self._session_copy_btn.clicked.connect(self._copy_session_vars)
        self._session_copy_btn.setEnabled(False)
        session_header.addWidget(self._session_copy_btn)
        self._session_add_btn = QPushButton("Add")
        self._session_add_btn.setToolTip("Add or update a custom session variable")
        self._session_add_btn.clicked.connect(self._add_session_var)
        session_header.addWidget(self._session_add_btn)
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
        self._session_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._session_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._session_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._session_table.customContextMenuRequested.connect(self._show_session_context_menu)
        self._session_table.itemSelectionChanged.connect(self._on_session_selection)
        self._session_table.verticalHeader().setVisible(False)
        self._session_table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        session_layout.addWidget(self._session_table)

        self._session_group.toggled.connect(self._on_session_group_toggled)

        layout.addWidget(self._session_group)

        # ── Variable Groups (persisted in DB) ─────────────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)

        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.addWidget(QLabel("<b>Groups</b>"))

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

        self.groups_list = QListWidget()
        self.groups_list.itemSelectionChanged.connect(self._on_group_selected)
        self.groups_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.groups_list.customContextMenuRequested.connect(self._show_group_context_menu)
        left_layout.addWidget(self.groups_list)
        splitter.addWidget(left_widget)

        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.addWidget(QLabel("<b>Variables</b>"))

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

        self.variables_table = QTableWidget()
        self.variables_table.setColumnCount(3)
        self.variables_table.setHorizontalHeaderLabels(["Key", "Value", "Description"])
        self.variables_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.variables_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.variables_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        self.variables_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.variables_table.itemSelectionChanged.connect(self._on_variable_selected)
        self.variables_table.itemDoubleClicked.connect(self.edit_variable)
        right_layout.addWidget(self.variables_table)
        splitter.addWidget(right_widget)

        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(5)
        configure_splitter_persistence(
            splitter,
            settings_key="splitter/variables",
            default_sizes=[250, 550],
            settings=self._settings,
        )

        layout.addWidget(splitter)
        self._resize_global_table_to_content()
        self._resize_session_table_to_content()

    def _global_table_target_height(self) -> int:
        """Return a compact, content-based height for the global table."""
        header_h = self._global_table.horizontalHeader().height()
        frame_h = self._global_table.frameWidth() * 2
        row_h = self._global_table.verticalHeader().defaultSectionSize()
        visible_rows = max(
            _GLOBAL_TABLE_MIN_VISIBLE_ROWS,
            min(self._global_var_count, _GLOBAL_TABLE_MAX_VISIBLE_ROWS),
        )
        return frame_h + header_h + (row_h * visible_rows) + 2

    def _resize_global_table_to_content(self) -> None:
        """Keep global variables table compact while still allowing scrolling."""
        self._global_table.setFixedHeight(self._global_table_target_height())

    def _session_table_target_height(self) -> int:
        """Return a compact, content-based height for the session table."""
        header_h = self._session_table.horizontalHeader().height()
        frame_h = self._session_table.frameWidth() * 2
        row_h = self._session_table.verticalHeader().defaultSectionSize()
        visible_rows = max(
            _SESSION_TABLE_MIN_VISIBLE_ROWS,
            min(self._session_var_count, _SESSION_TABLE_MAX_VISIBLE_ROWS),
        )
        return frame_h + header_h + (row_h * visible_rows) + 2

    def _resize_session_table_to_content(self) -> None:
        """Keep session table height compact while still allowing scrolling."""
        if not self._session_group.isChecked():
            self._session_table.setFixedHeight(0)
            return
        self._session_table.setFixedHeight(self._session_table_target_height())

    def _on_session_group_toggled(self, checked: bool) -> None:
        """Collapse/expand session table area when the group is toggled."""
        self._session_table.setVisible(checked)
        self._resize_session_table_to_content()

    # ── Public refresh methods ────────────────────────────────────────────────

    def refresh(self) -> None:
        """Reload groups and current variables."""
        self.refresh_global_vars()
        self.refresh_groups()
        if self.current_group_id:
            self.refresh_variables()

    def refresh_global_vars(self) -> None:
        """Rebuild global variables table from the database."""
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

    def refresh_groups(self) -> None:
        """Rebuild the groups list from the database.

        Signals and screen updates are suppressed during the rebuild.
        """
        self.groups_list.blockSignals(True)
        self.groups_list.setUpdatesEnabled(False)
        try:
            self.groups_list.clear()
            self.current_group_id = None
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
        """Rebuild the variables table for the selected group.

        The interpolation context (active env + OS env + session vars) is
        built **once** before the loop so every row re-uses the same snapshot
        rather than making fresh DB and OS calls per row.
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

        # Build interpolation context once — not inside the per-row loop.
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

                # Tooltip: show raw vs interpolated value for quick preview.
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

    # ── Interpolation context ─────────────────────────────────────────────────

    def _build_interp_context(self) -> dict[str, str]:
        """Build the variable resolution map used for tooltip previews.

        Mirrors the request-time resolution order (magic/global/env/collection/
        OS/session) and overlays path params for tooltip previews.
        Each step silently degrades on failure so a broken source does not
        prevent the rest from contributing.
        """
        try:
            rp = getattr(self.window(), "request_panel", None)
            session_vars = rp.get_session_vars() if rp is not None else {}
            collection_id = getattr(getattr(rp, "current_request", None), "collection_id", None)
            interp_vars, _sources = collect_interpolation_variables_detailed(
                self.db,
                collection_id=collection_id,
                session_vars=session_vars,
            )
            if rp is not None:
                path_table = getattr(rp, "path_params_table", None)
                if path_table is not None:
                    interp_vars.update(path_table.get_all_data())
            return interp_vars
        except Exception as exc:
            logger.debug("Tooltip: failed to build interpolation context: %s", exc)
            return {}

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
        name, ok = QInputDialog.getText(self, "New Variable Group", "Group name:")
        if not ok or not name:
            return
        description, ok = QInputDialog.getText(
            self, "New Variable Group", "Description (optional):"
        )
        if not ok:
            return
        try:
            self._mgr.create_group(name, description or "")
            self.refresh_groups()
            self.variables_changed.emit()
            QMessageBox.information(self, "Success", f"Variable group '{name}' created")
        except Exception as exc:
            logger.error("Failed to create group %r: %s", name, exc, exc_info=True)
            QMessageBox.critical(self, "Error", f"Failed to create group: {exc}")

    def delete_group(self) -> None:
        if not self.current_group_id:
            return
        selected = self.groups_list.selectedItems()
        if not selected:
            return
        group_name = selected[0].text()
        if not confirm_yes_no(
            self,
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
            QMessageBox.critical(self, "Error", f"Failed to delete group: {exc}")

    def _show_group_context_menu(self, position) -> None:
        item = self.groups_list.itemAt(position)
        if not item:
            return
        assert item is not None
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
                    "variables_group", aid, cb
                ),
            )
        menu.exec(self.groups_list.viewport().mapToGlobal(position))

    def _rename_group(self, item: QListWidgetItem) -> None:
        group_id = item.data(Qt.ItemDataRole.UserRole)
        old_name = item.text()
        new_name, ok = QInputDialog.getText(self, "Rename Group", "New name:", text=old_name)
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
        dialog = VariableDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        key, value, description = dialog.get_values()
        if not key:
            QMessageBox.warning(self, "Error", "Variable key is required")
            return
        try:
            self._mgr.add_variable(self.current_group_id, key, value, description)
            self.refresh_variables()
            self.variables_changed.emit()
        except Exception as exc:
            logger.error("Failed to add variable %r: %s", key, exc, exc_info=True)
            QMessageBox.critical(self, "Error", f"Failed to add variable: {exc}")

    def edit_variable(self) -> None:
        if not self.current_group_id:
            return
        selected_row = self.variables_table.currentRow()
        if selected_row < 0:
            return
        key = self.variables_table.item(selected_row, 0).text()
        value = self.variables_table.item(selected_row, 1).text()
        description = self.variables_table.item(selected_row, 2).text()
        dialog = VariableDialog(self, key, value, description)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        new_key, new_value, new_description = dialog.get_values()
        if not new_key:
            QMessageBox.warning(self, "Error", "Variable key is required")
            return
        try:
            if new_key != key:
                self._mgr.remove_variable(self.current_group_id, key)
            self._mgr.add_variable(self.current_group_id, new_key, new_value, new_description)
            self.refresh_variables()
            self.variables_changed.emit()
        except Exception as exc:
            logger.error("Failed to update variable %r: %s", key, exc, exc_info=True)
            QMessageBox.critical(self, "Error", f"Failed to update variable: {exc}")

    def remove_variable(self) -> None:
        if not self.current_group_id:
            return
        selected_row = self.variables_table.currentRow()
        if selected_row < 0:
            return
        key = self.variables_table.item(selected_row, 0).text()
        if not confirm_yes_no(
            self, "Confirm Delete", f"Are you sure you want to delete variable '{key}'?"
        ):
            return
        try:
            self._mgr.remove_variable(self.current_group_id, key)
            self.refresh_variables()
            self.variables_changed.emit()
        except Exception as exc:
            logger.error("Failed to remove variable %r: %s", key, exc, exc_info=True)
            QMessageBox.critical(self, "Error", f"Failed to remove variable: {exc}")

    # ── Session Variables ─────────────────────────────────────────────────────

    def refresh_session_vars(self, session_vars: dict) -> None:
        """Repopulate the session variables table.

        Called by ``RequestPanel.session_vars_changed`` signal.
        Screen updates are suppressed during the rebuild.
        """
        self._session_var_count = len(session_vars)

        self._session_table.setSortingEnabled(False)
        self._session_table.setUpdatesEnabled(False)
        try:
            self._session_table.setRowCount(self._session_var_count)
            for row, (key, value) in enumerate(sorted(session_vars.items())):
                key_item = QTableWidgetItem(key)
                key_item.setFlags(key_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                val_item = QTableWidgetItem(str(value))
                val_item.setFlags(val_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self._session_table.setItem(row, 0, key_item)
                self._session_table.setItem(row, 1, val_item)
        finally:
            self._session_table.setUpdatesEnabled(True)
            self._session_table.setSortingEnabled(True)

        has_vars = self._session_var_count > 0
        self._session_clear_btn.setEnabled(has_vars)
        self._session_copy_btn.setEnabled(has_vars)
        self._session_delete_btn.setEnabled(False)
        noun = "variable" if self._session_var_count == 1 else "variables"
        self._session_count_label.setText(
            f"{self._session_var_count} captured {noun}" if has_vars else "No captured variables"
        )
        if has_vars and not self._session_group.isChecked():
            self._session_group.setChecked(True)
        self._resize_session_table_to_content()
        self._update_tab_badge()

    def _on_session_selection(self) -> None:
        self._session_delete_btn.setEnabled(bool(self._session_table.selectedItems()))

    def _on_clear_session(self) -> None:
        if self._session_var_count > 0:
            self.clear_session_requested.emit()

    def _current_session_vars(self) -> dict[str, str]:
        """Return current session vars from the table as a key/value dict."""
        result: dict[str, str] = {}
        for row in range(self._session_table.rowCount()):
            key_item = self._session_table.item(row, 0)
            val_item = self._session_table.item(row, 1)
            if key_item and val_item:
                result[key_item.text()] = val_item.text()
        return result

    def _resolve_request_panel(self):
        """Return the nearest RequestPanel host exposing ``request_panel`` if available."""
        host = self.window()
        rp = getattr(host, "request_panel", None)
        if rp is not None:
            return rp

        host = self.parent()
        while host is not None:
            rp = getattr(host, "request_panel", None)
            if rp is not None:
                return rp
            host = host.parent()
        return None

    def _publish_session_var(self, rp, key: str, value: str) -> bool:
        """Write a session variable into a request-panel-like object."""
        session_vars = getattr(rp, "_session_vars", None)
        if isinstance(session_vars, dict):
            session_vars[key] = value
            changed = getattr(rp, "session_vars_changed", None)
            emit = getattr(changed, "emit", None)
            if callable(emit):
                emit(dict(session_vars))
            return True

        setter = getattr(rp, "set_session_var", None)
        if callable(setter):
            setter(key, value)
            return True

        return False

    def _delete_published_session_var(self, rp, key: str) -> bool:
        """Remove a session variable from a request-panel-like object."""
        session_vars = getattr(rp, "_session_vars", None)
        if isinstance(session_vars, dict):
            session_vars.pop(key, None)
            changed = getattr(rp, "session_vars_changed", None)
            emit = getattr(changed, "emit", None)
            if callable(emit):
                emit(dict(session_vars))
            return True

        deleter = getattr(rp, "delete_session_var", None)
        if callable(deleter):
            deleter(key)
            return True

        return False

    def _add_session_var(self) -> None:
        """Prompt for a custom session variable and publish it to RequestPanel."""
        key, ok = QInputDialog.getText(self, "Add Session Variable", "Variable name:")
        if not ok:
            return
        key = key.strip()
        if not key:
            QMessageBox.warning(self, "Error", "Variable name is required")
            return

        value, ok = QInputDialog.getText(self, "Add Session Variable", "Value:")
        if not ok:
            return

        rp = self._resolve_request_panel()
        try:
            key = Validator.validate_variable_name(key)
        except ValidationError as exc:
            QMessageBox.warning(self, "Invalid Variable Name", str(exc))
            return

        if rp is not None and self._publish_session_var(rp, key, value):
            return

        # Fallback for tests or unusual embedding: update panel-local view.
        session_vars = self._current_session_vars()
        session_vars[key] = value
        self.refresh_session_vars(session_vars)

    def _delete_session_var(self) -> None:
        row = self._session_table.currentRow()
        if row < 0:
            return
        key_item = self._session_table.item(row, 0)
        if not key_item:
            return
        key = key_item.text()
        try:
            rp = self._resolve_request_panel()
            if rp is not None:
                self._delete_published_session_var(rp, key)
        except Exception as exc:
            logger.debug("Failed to delete session var %r: %s", key, exc)

    @staticmethod
    def _is_secret_like(key: str) -> bool:
        key_lower = key.lower()
        return any(
            token in key_lower
            for token in (
                "token",
                "secret",
                "password",
                "passwd",
                "apikey",
                "api_key",
                "credential",
                "private",
            )
        )

    def _copy_session_vars(self) -> None:
        lines = []
        has_secret = False
        for r in range(self._session_table.rowCount()):
            key_item = self._session_table.item(r, 0)
            val_item = self._session_table.item(r, 1)
            if not key_item or not val_item:
                continue
            key = key_item.text()
            value = val_item.text()
            if self._is_secret_like(key):
                has_secret = True
                value = "<redacted>"
            lines.append(f"{key}={value}")
        if lines:
            if has_secret:
                logger.warning(
                    "Copying session variables with secret-like keys; values were redacted"
                )
            clipboard = QApplication.clipboard()
            if clipboard:
                clipboard.setText("\n".join(lines))

    def _show_session_context_menu(self, position) -> None:
        item = self._session_table.itemAt(position)
        if not item:
            return
        row = item.row()
        menu = QMenu()
        action_specs = [
            (
                "copy_name",
                "Copy Variable Name",
                lambda: self._copy_session_key_at_row(row),
                False,
            ),
            (
                "copy_value",
                "Copy Value",
                lambda: self._copy_session_value_at_row(row),
                False,
            ),
            (
                "delete",
                "Delete",
                lambda: self._delete_session_var_at_row(row),
                True,
            ),
        ]
        ordered = self._ordered_context_actions("variables_session", action_specs)
        added_destructive_separator = False
        for action_id, label, callback, is_destructive in ordered:
            if is_destructive and not added_destructive_separator:
                menu.addSeparator()
                added_destructive_separator = True
            menu.addAction(
                label,
                lambda aid=action_id, cb=callback: self._run_context_action(
                    "variables_session", aid, cb
                ),
            )
        menu.exec(self._session_table.viewport().mapToGlobal(position))

    def _copy_session_key_at_row(self, row: int) -> None:
        clipboard = QApplication.clipboard()
        ki = self._session_table.item(row, 0)
        if ki and clipboard:
            clipboard.setText(ki.text())

    def _copy_session_value_at_row(self, row: int) -> None:
        clipboard = QApplication.clipboard()
        vi = self._session_table.item(row, 1)
        ki = self._session_table.item(row, 0)
        if not (vi and clipboard and ki):
            return
        if self._is_secret_like(ki.text()):
            if not confirm_yes_no(
                self,
                "Copy Secret Value",
                f"Copy the secret value for '{ki.text()}' to the clipboard?",
            ):
                return
        clipboard.setText(vi.text())

    def _delete_session_var_at_row(self, row: int) -> None:
        self._session_table.setCurrentItem(self._session_table.item(row, 0))
        self._delete_session_var()

    def _context_action_usage_count(self, context: str, action_id: str) -> int:
        tracker = getattr(self.window(), "_ui_usage_tracker", None)
        if tracker is None:
            return 0
        try:
            return tracker.get_count(
                category="context_menu",
                context=context,
                element_id=f"action.{action_id}",
            )
        except Exception:
            logger.debug(
                "Failed to get context action usage for %s/%s", context, action_id, exc_info=True
            )
            return 0

    def _record_context_action_usage(self, context: str, action_id: str) -> None:
        tracker = getattr(self.window(), "_ui_usage_tracker", None)
        if tracker is None:
            return
        try:
            tracker.record(
                f"action.{action_id}",
                category="context_menu",
                context=context,
            )
        except Exception:
            logger.debug(
                "Failed to record context action usage for %s/%s", context, action_id, exc_info=True
            )

    def _run_context_action(self, context: str, action_id: str, callback) -> None:
        self._record_context_action_usage(context, action_id)
        callback()

    def _ordered_context_actions(self, context: str, action_specs: list[tuple]) -> list[tuple]:
        """Sort non-destructive actions by usage while keeping destructive actions last."""
        safe = []
        destructive = []
        for idx, spec in enumerate(action_specs):
            action_id, label, callback, is_destructive = spec
            if is_destructive:
                destructive.append((idx, spec))
                continue
            count = self._context_action_usage_count(context, action_id)
            safe.append((-count, idx, spec))
        safe.sort(key=lambda row: (row[0], row[1]))
        return [row[2] for row in safe] + [row[1] for row in destructive]

    # ── Tab badge ─────────────────────────────────────────────────────────────

    def _update_tab_badge(self) -> None:
        """Update the Variables tab title to show a session variable count badge."""
        try:
            tab_widget = self.parent()
            while tab_widget and not isinstance(tab_widget, QTabWidget):
                tab_widget = tab_widget.parent()
            if not isinstance(tab_widget, QTabWidget):
                return
            idx = tab_widget.indexOf(self)
            if idx < 0:
                return
            tab_widget.setTabText(
                idx,
                f"Variables ({self._session_var_count})"
                if self._session_var_count > 0
                else "Variables",
            )
        except Exception as exc:
            logger.debug("Failed to update tab badge: %s", exc)
