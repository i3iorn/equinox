"""Environment management dialog — full variable CRUD."""

from collections.abc import Iterator
from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from equinox.gui.dialogs._list_form_dialog_mixin import ListFormDialogMixin
from equinox.gui.dialogs.environment_dialog.dotenv_importer import (
    DotenvImporter,
    DotenvImportResult,
)
from equinox.gui.theme import Colors
from equinox.storage import Database, EnvironmentManager

_MAX_DOTENV_IMPORT_BYTES = 2 * 1024 * 1024


class EnvironmentDialog(ListFormDialogMixin, QDialog):
    """Manage environments and their variables.

    Variables can be added, edited inline, and removed.  Changes are written
    to the database immediately when the user clicks *Save Variables*.
    Activating an environment marks it as the source for ``{{VAR}}``
    interpolation throughout the app.
    """

    environment_changed = pyqtSignal()  # emitted after any structural change

    def __init__(self, db: Database, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.db = db
        self.env_manager = EnvironmentManager(db)
        self._current_env_id: int | None = None
        self._current_id: int | None = None  # Alias for mixin
        self._dirty = False  # unsaved variable edits

        # DirtyDialogMixin requirements
        self._save_callback = self._save_variables

        self.setWindowTitle("Manage Environments")
        self.setMinimumSize(780, 520)
        self._init_ui()
        self._list_widget = self.env_list
        self._refresh_list()

    # ── UI ────────────────────────────────────────────────────────────

    def _init_ui(self) -> None:
        """Initializes and sets up the user interface layout."""
        root = QVBoxLayout(self)
        root.setSpacing(8)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        left = self._build_left_panel()
        right = self._build_right_panel()

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([240, 540])
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(5)
        root.addWidget(splitter, 1)
        self._setup_bottom_buttons(root)

    def _build_left_panel(self) -> QWidget:
        """Constructs the left panel containing environment list and controls."""
        left = QWidget()
        llay = QVBoxLayout(left)
        llay.setContentsMargins(0, 0, 4, 0)

        llay.addWidget(QLabel("<b>Environments</b>"))

        self.env_list = QListWidget()
        self.env_list.currentItemChanged.connect(self._on_item_selected)
        self.env_list.itemDoubleClicked.connect(self._rename_environment)
        llay.addWidget(self.env_list, 1)

        # Environment Action Buttons
        env_btns = QHBoxLayout()
        self.new_btn = QPushButton("New…")
        self.rename_btn = QPushButton("Rename…")
        self.activate_btn = QPushButton("Activate")
        self.delete_btn = QPushButton("Delete")

        for b in (self.new_btn, self.rename_btn, self.activate_btn, self.delete_btn):
            b.setFixedHeight(26)
            env_btns.addWidget(b)

        self.rename_btn.setEnabled(False)
        self.activate_btn.setEnabled(False)
        self.delete_btn.setEnabled(False)
        llay.addLayout(env_btns)

        # Connect signals
        self.new_btn.clicked.connect(self._create_environment)
        self.rename_btn.clicked.connect(self._rename_environment)
        self.activate_btn.clicked.connect(self._activate_environment)
        self.delete_btn.clicked.connect(self._delete_environment)

        return left

    def _build_right_panel(self) -> QWidget:
        """Constructs the right panel containing the variable table and controls."""
        right = QWidget()
        rlay = QVBoxLayout(right)
        rlay.setContentsMargins(4, 0, 0, 0)

        # Header Label
        self._setup_header(rlay)

        # Variable Table Setup
        self._setup_variable_table(rlay)

        # Variable Action Buttons
        self._setup_variable_controls(rlay)

        return right

    def _setup_header(self, rlay: QVBoxLayout) -> None:
        """Sets up the header label for the right panel."""
        self.var_header = QLabel(
            f"<b>Variables</b>  <span style='color:{Colors.FG_MUTED};'>(select an environment)</span>",
        )
        rlay.addWidget(self.var_header)

    def _setup_variable_table(self, rlay: QVBoxLayout) -> None:
        """Configures and adds the variable table to the layout."""
        self.var_table = QTableWidget()
        self.var_table.setColumnCount(3)
        self.var_table.setHorizontalHeaderLabels(["Variable", "Value", "Secret"])

        # Configure Header Resize Modes
        hdr = self.var_table.horizontalHeader()
        if hdr is not None:
            hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
            hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
            hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)

        self.var_table.setColumnWidth(2, 58)
        if hdr is not None:
            hdr.setDefaultSectionSize(180)

        # Other Table Settings
        v_hdr = self.var_table.verticalHeader()
        if v_hdr is not None:
            v_hdr.setVisible(False)
        self.var_table.setAlternatingRowColors(True)
        self.var_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.var_table.setEnabled(False)
        self.var_table.itemChanged.connect(self._on_var_changed)
        rlay.addWidget(self.var_table, 1)

    def _setup_variable_controls(self, rlay: QVBoxLayout) -> None:
        """Sets up the action buttons for variable management."""
        var_btns = QHBoxLayout()
        self.add_var_btn = QPushButton("Add Variable")
        self.remove_var_btn = QPushButton("Remove Selected")
        self.import_dotenv_btn = QPushButton("Import .env…")
        self.import_dotenv_btn.setToolTip("Load variables from a .env file (merged with existing)")
        self.save_vars_btn = QPushButton("💾  Save Variables")

        for b in (
            self.add_var_btn,
            self.remove_var_btn,
            self.import_dotenv_btn,
            self.save_vars_btn,
        ):
            b.setEnabled(False)
            var_btns.addWidget(b)

        var_btns.addStretch()
        rlay.addLayout(var_btns)

        # Connect signals
        self.add_var_btn.clicked.connect(self._add_variable_row)
        self.remove_var_btn.clicked.connect(self._remove_selected_variable)
        self.import_dotenv_btn.clicked.connect(self._import_dotenv)
        self.save_vars_btn.clicked.connect(self._save_variables)

    def _setup_bottom_buttons(self, parent_layout: QVBoxLayout) -> None:
        """Adds the final dialog button box to the main layout."""
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self._on_close)
        parent_layout.addWidget(btns)

    def _build_list_items(self) -> Iterator[tuple[int, str, dict[str, Any]]]:
        """Yield (item_id, label, kwargs) for each environment."""
        envs = self.env_manager.list_environments()
        for env in envs:
            name = env["name"]
            active = bool(env.get("is_active"))
            label = ("✓  " if active else "     ") + name
            kwargs: dict[str, Any] = {}
            if active:
                from equinox.gui.theme import Colors

                kwargs["fg_color"] = Colors.GREEN
                font = QFont()
                font.setBold(True)
                kwargs["font"] = font
            yield env["id"], label, kwargs

    def _on_list_item_selected(self, env_id: int) -> None:
        """Load the variables for the environment."""
        self._current_env_id = env_id
        self._load_variables(env_id)

    def _set_form_enabled(self, enabled: bool) -> None:
        """Enable/disable the variable table and action buttons."""
        self.var_table.setEnabled(enabled)
        # Buttons will be synced by _sync_buttons

    def _sync_buttons(self) -> None:
        """Update button states based on current selection."""
        has = self._current_id is not None
        for btn in (
            self.rename_btn,
            self.activate_btn,
            self.delete_btn,
            self.add_var_btn,
            self.remove_var_btn,
            self.import_dotenv_btn,
            self.save_vars_btn,
        ):
            btn.setEnabled(has)
        if not has:
            self.var_table.setRowCount(0)

    def _load_variables(self, env_id: int) -> None:
        env = self.env_manager.get_environment(env_id)
        if not env:
            return
        variables = env.get("variables", {})
        secret_keys = set(env.get("secret_keys") or [])
        name = env["name"]
        active_tag = (
            f" <span style='color:{Colors.GREEN};'>(active)</span>" if env.get("is_active") else ""
        )
        self.var_header.setText(
            f"<b>Variables — {name}</b>{active_tag}"
            f"  <span style='color:{Colors.FG_MUTED};'>"
            f"{len(variables)} variable(s)</span>",
        )
        self.var_table.blockSignals(True)
        self.var_table.setUpdatesEnabled(False)
        try:
            self.var_table.setRowCount(0)
            for key, value in variables.items():
                self._append_var_row(key, str(value), secret=key in secret_keys)
        finally:
            self.var_table.setUpdatesEnabled(True)
            self.var_table.blockSignals(False)
        self._dirty = False
        self._update_save_btn()

    def _on_var_changed(self, _item: QTableWidgetItem) -> None:
        self._dirty = True
        self._update_save_btn()

    def _update_save_btn(self) -> None:
        if self._dirty:
            self.save_vars_btn.setText("💾  Save Variables *")
        else:
            self.save_vars_btn.setText("💾  Save Variables")

    def _append_var_row(self, key: str = "", value: str = "", secret: bool = False) -> None:
        row = self.var_table.rowCount()
        self.var_table.insertRow(row)
        self.var_table.setItem(row, 0, QTableWidgetItem(key))
        self.var_table.setItem(row, 1, QTableWidgetItem(value))
        secret_item = QTableWidgetItem()
        secret_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
        secret_item.setCheckState(Qt.CheckState.Checked if secret else Qt.CheckState.Unchecked)
        secret_item.setToolTip("Mark as secret — value will be masked in other views")
        self.var_table.setItem(row, 2, secret_item)

    def _add_variable_row(self) -> None:
        if self._current_env_id is None:
            return
        self.var_table.blockSignals(True)
        self._append_var_row()
        self.var_table.blockSignals(False)
        row = self.var_table.rowCount() - 1
        self.var_table.setCurrentCell(row, 0)
        self.var_table.editItem(self.var_table.item(row, 0))
        self._dirty = True
        self._update_save_btn()

    def _remove_selected_variable(self) -> None:
        rows = sorted({i.row() for i in self.var_table.selectedItems()}, reverse=True)
        if not rows:
            QMessageBox.information(self, "No Selection", "Select one or more rows to remove.")
            return
        self.var_table.blockSignals(True)
        for row in rows:
            self.var_table.removeRow(row)
        self.var_table.blockSignals(False)
        self._dirty = True
        self._update_save_btn()

    def _save_variables(self) -> bool:
        if self._current_env_id is None:
            return False
        variables: dict[str, str] = {}
        secret_keys: list[str] = []
        errors = []
        for row in range(self.var_table.rowCount()):
            k_item = self.var_table.item(row, 0)
            v_item = self.var_table.item(row, 1)
            s_item = self.var_table.item(row, 2)
            key = k_item.text().strip() if k_item else ""
            value = v_item.text() if v_item else ""
            if not key:
                continue  # skip blank-key rows
            if key in variables:
                errors.append(f"Duplicate key: '{key}'")
            else:
                variables[key] = value
                if s_item and s_item.checkState() == Qt.CheckState.Checked:
                    secret_keys.append(key)

        if errors:
            QMessageBox.warning(self, "Duplicate Keys", "\n".join(errors))
            return False

        try:
            self.env_manager.update_environment(
                self._current_env_id,
                variables=variables,
                secret_keys=secret_keys,
            )
            self._dirty = False
            self._update_save_btn()
            self._load_variables(self._current_env_id)  # refresh count in header
            self.environment_changed.emit()
            try:
                win = self.window()
                status = win.statusBar() if win is not None else None  # type: ignore[attr-defined]
                if status is not None:
                    status.showMessage("Variables saved", 3000)
            except Exception:
                pass
            return True
        except Exception as exc:
            QMessageBox.critical(self, "Save Failed", str(exc))
            return False

    # ── Environment management ────────────────────────────────────────

    def _create_environment(self) -> None:
        name, ok = QInputDialog.getText(self, "New Environment", "Environment name:")
        if not ok or not name.strip():
            return
        try:
            env_id = self.env_manager.create_environment(name.strip(), {})
            self.environment_changed.emit()
            self._refresh_list(select_id=env_id)
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to create environment: {exc}")

    def _rename_environment(self, _item: Any = None) -> None:
        items = self.env_list.selectedItems()
        if not items:
            return
        env_id = items[0].data(Qt.ItemDataRole.UserRole)
        old_name = items[0].text().lstrip("✓").strip()
        new_name, ok = QInputDialog.getText(self, "Rename Environment", "New name:", text=old_name)
        if not ok or not new_name.strip() or new_name.strip() == old_name:
            return
        try:
            self.env_manager.update_environment(env_id, name=new_name.strip())
            self.environment_changed.emit()
            self._refresh_list(select_id=env_id)
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to rename: {exc}")

    def _activate_environment(self) -> None:
        items = self.env_list.selectedItems()
        if not items:
            return
        env_id = items[0].data(Qt.ItemDataRole.UserRole)
        try:
            self.env_manager.set_active_environment(env_id)
            self.environment_changed.emit()
            self._refresh_list(select_id=env_id)
            self._load_variables(env_id)  # refresh active tag in header
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to activate: {exc}")

    def _delete_environment(self) -> None:
        items = self.env_list.selectedItems()
        if not items:
            return
        name = items[0].text().lstrip("✓").strip()
        env_id = items[0].data(Qt.ItemDataRole.UserRole)
        reply = QMessageBox.question(
            self,
            "Confirm Delete",
            f"Delete environment '{name}'?\n\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            self.env_manager.delete_environment(env_id)
            self._current_env_id = None
            self._dirty = False
            self.environment_changed.emit()
            self._refresh_list()
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to delete: {exc}")

    def _import_dotenv(self) -> None:
        if self._current_env_id is None:
            return

        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import .env File",
            "",
            "Env Files (*.env *.env.*);;Text Files (*.txt);;All Files (*)",
        )
        if not path:
            return

        importer = DotenvImporter()

        try:
            content = importer.load_file(path)
            new_vars = importer.parse(content)
        except Exception as exc:
            QMessageBox.critical(self, "Import Error", str(exc))
            return

        existing = self._get_existing_keys_with_values()
        result = importer.diff(new_vars, existing)

        self._apply_import_result(result)
        self._dirty = True
        self._update_save_btn()
        self._show_import_summary(result)

    def _get_existing_keys_with_values(self) -> dict[str, str]:
        data = {}
        for r in range(self.var_table.rowCount()):
            k = self.var_table.item(r, 0)
            v = self.var_table.item(r, 1)
            if k and v:
                data[k.text()] = v.text()
        return data

    def _apply_import_result(self, result: DotenvImportResult) -> None:
        self.var_table.blockSignals(True)
        try:
            for key, value in result.updated.items():
                row = self._find_row_for_key(key)
                if row is not None:
                    self.var_table.item(row, 1).setText(value)  # type: ignore[union-attr]

            for key, value in result.added.items():
                self._append_var_row(key, value)
        finally:
            self.var_table.blockSignals(False)

    def _find_row_for_key(self, key: str) -> int | None:
        for r in range(self.var_table.rowCount()):
            k_item = self.var_table.item(r, 0)
            if k_item and k_item.text() == key:
                return r
        return None

    def _get_existing_keys(self) -> dict[str, int]:
        """Retrieves the current variables already stored in the table."""
        existing_keys: dict[str, int] = {}
        for r in range(self.var_table.rowCount()):
            k_item = self.var_table.item(r, 0)
            if k_item:
                existing_keys[k_item.text()] = r
        return existing_keys

    def _update_table_with_variables(
        self,
        new_vars: dict[str, str],
        existing_keys: dict[str, int],
    ) -> None:
        """Updates or appends rows in the QTableWidget based on new variables."""
        self.var_table.blockSignals(True)
        self.var_table.setUpdatesEnabled(False)

        added = 0
        updated = 0
        for key, value in new_vars.items():
            if key in existing_keys:
                r = existing_keys[key]
                v_item = self.var_table.item(r, 1)
                if v_item:
                    v_item.setText(value)
                updated += 1
            else:
                self._append_var_row(key, value)
                added += 1

        self.var_table.setUpdatesEnabled(True)
        self.var_table.blockSignals(False)

    def _show_import_summary(self, imported: DotenvImportResult) -> None:
        """Displays a summary message to the user after the import operation."""
        QMessageBox.information(
            self,
            "Import .env",
            f"Imported {imported.added} variable(s): {imported.added} new, {imported.updated} updated.\n\n"
            "Click 'Save Variables' to persist the changes.",
        )
