"""Session variables section for VariablesPanel."""

from __future__ import annotations

import logging
from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from equinox.core.exceptions import ValidationError
from equinox.core.validation import Validator

from ..ui_common import confirm_yes_no, create_muted_label

logger = logging.getLogger(__name__)

_SESSION_TABLE_MAX_VISIBLE_ROWS = 4
_SESSION_TABLE_MIN_VISIBLE_ROWS = 1


class _SessionVarsMixin:
    """Mixin providing the Session Variables section UI and logic."""

    def _build_session_vars_section(self) -> QGroupBox:
        """Construct the Session Variables group box and wire all signals.

        Assigns widget references to ``self`` so handler methods can reach them.
        Returns the constructed ``QGroupBox``.
        """
        self._session_group = QGroupBox("Session Variables")
        self._session_group.setCheckable(True)
        self._session_group.setChecked(True)
        self._session_group.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum
        )
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
        self._session_table.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum
        )
        session_layout.addWidget(self._session_table)

        self._session_group.toggled.connect(self._on_session_group_toggled)
        return self._session_group

    # ── Height helpers ────────────────────────────────────────────────────────

    def _session_table_target_height(self) -> int:
        """Return a compact, content-based height for the session variables table."""
        header_h = self._session_table.horizontalHeader().height()
        frame_h = self._session_table.frameWidth() * 2
        row_h = self._session_table.verticalHeader().defaultSectionSize()
        visible_rows = max(
            _SESSION_TABLE_MIN_VISIBLE_ROWS,
            min(self._session_var_count, _SESSION_TABLE_MAX_VISIBLE_ROWS),
        )
        return frame_h + header_h + (row_h * visible_rows) + 2

    def _resize_session_table_to_content(self) -> None:
        """Keep the session table compact while still allowing scrolling."""
        if not self._session_group.isChecked():
            self._session_table.setFixedHeight(0)
            return
        self._session_table.setFixedHeight(self._session_table_target_height())

    def _on_session_group_toggled(self, checked: bool) -> None:
        """Collapse or expand the session table when the group checkbox is toggled."""
        self._session_table.setVisible(checked)
        self._resize_session_table_to_content()

    # ── Data refresh ──────────────────────────────────────────────────────────

    def refresh_session_vars(self, session_vars: dict[str, str]) -> None:
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

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_session_selection(self) -> None:
        self._session_delete_btn.setEnabled(bool(self._session_table.selectedItems()))

    def _on_clear_session(self) -> None:
        if self._session_var_count > 0:
            self.clear_session_requested.emit()

    def _current_session_vars(self) -> dict[str, str]:
        """Return the current session vars from the table as a key/value dict."""
        result: dict[str, str] = {}
        for row in range(self._session_table.rowCount()):
            key_item = self._session_table.item(row, 0)
            val_item = self._session_table.item(row, 1)
            if key_item and val_item:
                result[key_item.text()] = val_item.text()
        return result

    # ── RequestPanel integration ──────────────────────────────────────────────

    def _resolve_request_panel(self) -> Any:
        """Return the nearest ``RequestPanel`` host via widget ancestry."""
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

    def _publish_session_var(self, rp: Any, key: str, value: str) -> bool:
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

    def _delete_published_session_var(self, rp: Any, key: str) -> bool:
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
        try:
            key = Validator.validate_variable_name(key)
        except ValidationError as exc:
            QMessageBox.warning(self, "Invalid Variable Name", str(exc))
            return
        rp = self._resolve_request_panel()
        if rp is not None and self._publish_session_var(rp, key, value):
            return
        # Fallback for test environments or unusual embeddings.
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

    # ── Clipboard helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _is_secret_like(key: str) -> bool:
        """Return True if *key* looks like it holds a secret value."""
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
        """Copy all session variables to the clipboard as KEY=VALUE lines.

        Secret-like keys have their values redacted to avoid accidental leaks.
        """
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

    # ── Context menu ──────────────────────────────────────────────────────────

    def _show_session_context_menu(self, position: Any) -> None:
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

