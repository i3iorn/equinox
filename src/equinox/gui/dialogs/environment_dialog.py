"""Environment management dialog — full variable CRUD."""

from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QSplitter,
    QListWidget, QListWidgetItem, QPushButton,
    QInputDialog, QMessageBox, QLabel, QWidget,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QDialogButtonBox, QAbstractItemView, QFileDialog,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont

from typing import Optional

from equinox.core.dotenv import parse_dotenv as _parse_dotenv
from equinox.gui.theme import Colors
from equinox.gui.dialogs._list_form_dialog_mixin import ListFormDialogMixin
from equinox.storage import Database, EnvironmentManager


class EnvironmentDialog(ListFormDialogMixin, QDialog):
    """Manage environments and their variables.

    Variables can be added, edited inline, and removed.  Changes are written
    to the database immediately when the user clicks *Save Variables*.
    Activating an environment marks it as the source for ``{{VAR}}``
    interpolation throughout the app.
    """

    environment_changed = pyqtSignal()   # emitted after any structural change

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db = db
        self.env_manager = EnvironmentManager(db)
        self._current_env_id: Optional[int] = None
        self._current_id: Optional[int] = None  # Alias for mixin
        self._dirty = False          # unsaved variable edits

        # DirtyDialogMixin requirements
        self._save_callback = self._save_variables

        self.setWindowTitle("Manage Environments")
        self.setMinimumSize(780, 520)
        self._init_ui()
        # Set _list_widget after UI construction (required by ListFormDialogMixin)
        self._list_widget = self.env_list
        self._refresh_list()

    # ── UI ────────────────────────────────────────────────────────────

    def _init_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(8)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── Left: environment list ────────────────────────────────────
        left = QWidget()
        llay = QVBoxLayout(left)
        llay.setContentsMargins(0, 0, 4, 0)

        llay.addWidget(QLabel("<b>Environments</b>"))

        self.env_list = QListWidget()
        self.env_list.currentItemChanged.connect(self._on_item_selected)
        self.env_list.itemDoubleClicked.connect(self._rename_environment)
        llay.addWidget(self.env_list, 1)

        env_btns = QHBoxLayout()
        self.new_btn      = QPushButton("New…")
        self.rename_btn   = QPushButton("Rename…")
        self.activate_btn = QPushButton("Activate")
        self.delete_btn   = QPushButton("Delete")
        for b in (self.new_btn, self.rename_btn, self.activate_btn, self.delete_btn):
            b.setFixedHeight(26)
            env_btns.addWidget(b)
        self.rename_btn.setEnabled(False)
        self.activate_btn.setEnabled(False)
        self.delete_btn.setEnabled(False)
        llay.addLayout(env_btns)

        self.new_btn.clicked.connect(self._create_environment)
        self.rename_btn.clicked.connect(self._rename_environment)
        self.activate_btn.clicked.connect(self._activate_environment)
        self.delete_btn.clicked.connect(self._delete_environment)

        splitter.addWidget(left)

        # ── Right: variable table ─────────────────────────────────────
        right = QWidget()
        rlay = QVBoxLayout(right)
        rlay.setContentsMargins(4, 0, 0, 0)

        self.var_header = QLabel(
            f"<b>Variables</b>  <span style='color:{Colors.FG_MUTED};'>(select an environment)</span>"
        )
        rlay.addWidget(self.var_header)

        self.var_table = QTableWidget()
        self.var_table.setColumnCount(3)
        self.var_table.setHorizontalHeaderLabels(["Variable", "Value", "Secret"])
        hdr = self.var_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.var_table.setColumnWidth(2, 58)
        hdr.setDefaultSectionSize(180)
        self.var_table.verticalHeader().setVisible(False)
        self.var_table.setAlternatingRowColors(True)
        self.var_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.var_table.setEnabled(False)
        self.var_table.itemChanged.connect(self._on_var_changed)
        rlay.addWidget(self.var_table, 1)

        var_btns = QHBoxLayout()
        self.add_var_btn       = QPushButton("Add Variable")
        self.remove_var_btn    = QPushButton("Remove Selected")
        self.import_dotenv_btn = QPushButton("Import .env…")
        self.import_dotenv_btn.setToolTip("Load variables from a .env file (merged with existing)")
        self.save_vars_btn     = QPushButton("💾  Save Variables")
        for b in (self.add_var_btn, self.remove_var_btn, self.import_dotenv_btn, self.save_vars_btn):
            b.setEnabled(False)
            var_btns.addWidget(b)
        var_btns.addStretch()
        rlay.addLayout(var_btns)

        self.add_var_btn.clicked.connect(self._add_variable_row)
        self.remove_var_btn.clicked.connect(self._remove_selected_variable)
        self.import_dotenv_btn.clicked.connect(self._import_dotenv)
        self.save_vars_btn.clicked.connect(self._save_variables)

        splitter.addWidget(right)
        splitter.setSizes([240, 540])
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(5)
        root.addWidget(splitter, 1)

        # ── Bottom buttons ────────────────────────────────────────────
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self._on_close)
        root.addWidget(btns)

    # ── Environment list actions (ListFormDialogMixin template methods) ──

    def _build_list_items(self):
        """Yield (item_id, label, kwargs) for each environment."""
        from PyQt6.QtGui import QFont
        envs = self.env_manager.list_environments()
        for env in envs:
            name = env["name"]
            active = bool(env.get("is_active"))
            label = ("✓  " if active else "     ") + name
            kwargs = {}
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

    # ── Selection logic ───────────────────────────────────────────────
    # _apply_selection() inherited from ListFormDialogMixin
    # _on_item_selected(current, _prev) inherited from ListFormDialogMixin

    def _set_form_enabled(self, enabled: bool) -> None:
        """Enable/disable the variable table and action buttons."""
        self.var_table.setEnabled(enabled)
        # Buttons will be synced by _sync_buttons

    def _sync_buttons(self) -> None:
        """Update button states based on current selection."""
        has = self._current_id is not None
        for btn in (
            self.rename_btn, self.activate_btn, self.delete_btn,
            self.add_var_btn, self.remove_var_btn,
            self.import_dotenv_btn, self.save_vars_btn,
        ):
            btn.setEnabled(has)
        if not has:
            self.var_table.setRowCount(0)

    # ── Variable loading ───────────────────────────────────────────────

    def _load_variables(self, env_id: int) -> None:
        env = self.env_manager.get_environment(env_id)
        if not env:
            return
        variables   = env.get("variables", {})
        secret_keys = set(env.get("secret_keys") or [])
        name        = env["name"]
        active_tag  = (
            f" <span style='color:{Colors.GREEN};'>(active)</span>"
            if env.get("is_active") else ""
        )
        self.var_header.setText(
            f"<b>Variables — {name}</b>{active_tag}"
            f"  <span style='color:{Colors.FG_MUTED};'>"
            f"{len(variables)} variable(s)</span>"
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

    def _on_var_changed(self, _item) -> None:
        self._dirty = True
        self._update_save_btn()

    def _update_save_btn(self) -> None:
        if self._dirty:
            self.save_vars_btn.setText("💾  Save Variables *")
        else:
            self.save_vars_btn.setText("💾  Save Variables")

    # ── Variable table helpers ────────────────────────────────────────

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
        rows = sorted(
            {i.row() for i in self.var_table.selectedItems()}, reverse=True
        )
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
        variables:   dict[str, str] = {}
        secret_keys: list[str]      = []
        errors = []
        for row in range(self.var_table.rowCount()):
            k_item = self.var_table.item(row, 0)
            v_item = self.var_table.item(row, 1)
            s_item = self.var_table.item(row, 2)
            key   = k_item.text().strip() if k_item else ""
            value = v_item.text()         if v_item else ""
            if not key:
                continue   # skip blank-key rows
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
                self._current_env_id, variables=variables, secret_keys=secret_keys
            )
            self._dirty = False
            self._update_save_btn()
            self._load_variables(self._current_env_id)   # refresh count in header
            self.environment_changed.emit()
            try:
                self.window().statusBar().showMessage("Variables saved", 3000)
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

    def _rename_environment(self, _item=None) -> None:
        items = self.env_list.selectedItems()
        if not items:
            return
        env_id   = items[0].data(Qt.ItemDataRole.UserRole)
        old_name = items[0].text().lstrip("✓").strip()
        new_name, ok = QInputDialog.getText(
            self, "Rename Environment", "New name:", text=old_name
        )
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
            self._load_variables(env_id)   # refresh active tag in header
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to activate: {exc}")

    def _delete_environment(self) -> None:
        items = self.env_list.selectedItems()
        if not items:
            return
        name   = items[0].text().lstrip("✓").strip()
        env_id = items[0].data(Qt.ItemDataRole.UserRole)
        reply = QMessageBox.question(
            self, "Confirm Delete",
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
        """Import variables from a .env file into the current environment (merge)."""
        if self._current_env_id is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Import .env File", "",
            "Env Files (*.env *.env.*);;Text Files (*.txt);;All Files (*)"
        )
        if not path:
            return
        try:
            new_vars = _parse_dotenv(Path(path).read_text(encoding="utf-8", errors="replace"))
        except Exception as exc:
            QMessageBox.critical(self, "Import Error", f"Could not read file:\n{exc}")
            return

        if not new_vars:
            QMessageBox.information(
                self, "Import .env",
                "No KEY=VALUE pairs found in the selected file."
            )
            return

        existing_keys: dict[str, int] = {}
        for r in range(self.var_table.rowCount()):
            k_item = self.var_table.item(r, 0)
            if k_item:
                existing_keys[k_item.text()] = r

        self.var_table.blockSignals(True)
        self.var_table.setUpdatesEnabled(False)
        added = updated = 0
        try:
            for key, value in new_vars.items():
                if key in existing_keys:
                    v_item = self.var_table.item(existing_keys[key], 1)
                    if v_item:
                        v_item.setText(value)
                    updated += 1
                else:
                    self._append_var_row(key, value)
                    added += 1
        finally:
            self.var_table.setUpdatesEnabled(True)
            self.var_table.blockSignals(False)

        self._dirty = True
        self._update_save_btn()
        QMessageBox.information(
            self, "Import .env",
            f"Imported {len(new_vars)} variable(s): {added} new, {updated} updated.\n\n"
            "Click 'Save Variables' to persist the changes."
        )
