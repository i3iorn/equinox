"""Cookie Jar panel — view and manage persistent cookies."""

from __future__ import annotations

import logging

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from equinox.core.exceptions import ValidationError
from equinox.core.validation import Validator
from equinox.storage import Database
from equinox.storage.cookies import CookieJarManager

from .ui_common import confirm_yes_no, create_muted_label, create_panel_layout

__all__ = ["CookiesPanel"]

logger = logging.getLogger(__name__)


# ── Add-cookie dialog ─────────────────────────────────────────────────────────


class _AddCookieDialog(QDialog):
    """Simple dialog to add a cookie manually.

    Validates that *Name* is non-empty before accepting, displaying an inline
    error so the user knows exactly what to fix without the dialog closing.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add Cookie")
        self.setMinimumWidth(380)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.name_edit = QLineEdit()
        self.value_edit = QLineEdit()
        self.domain_edit = QLineEdit()
        self.path_edit = QLineEdit("/")
        self.secure_cb = QCheckBox()

        form.addRow("Name:", self.name_edit)
        form.addRow("Value:", self.value_edit)
        form.addRow("Domain:", self.domain_edit)
        form.addRow("Path:", self.path_edit)
        form.addRow("Secure:", self.secure_cb)
        layout.addLayout(form)

        self._error_label = QLabel()
        self._error_label.setVisible(False)
        layout.addWidget(self._error_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ── Validation ────────────────────────────────────────────────────────────

    def _validate_and_accept(self) -> None:
        """Validate fields; show an inline error instead of silently closing."""
        if not self.name_edit.text().strip():
            self._error_label.setText("Cookie name is required.")
            self._error_label.setVisible(True)
            self.name_edit.setFocus()
            return
        self._error_label.setVisible(False)
        self.accept()

    def values(self) -> dict[str, object]:
        """Return the form values as a plain dict."""
        return {
            "name": Validator.validate_cookie_name(self.name_edit.text().strip()),
            "value": Validator.validate_cookie_value(self.value_edit.text()),
            "domain": self.domain_edit.text().strip(),
            "path": self.path_edit.text().strip() or "/",
            "secure": self.secure_cb.isChecked(),
        }


# ── Main panel ────────────────────────────────────────────────────────────────


class CookiesPanel(QWidget):
    """Left-panel tab for managing the persistent cookie jar."""

    _COLUMNS = ("Name", "Value", "Domain", "Path", "Secure")

    # Column index that holds the DB row id in Qt.ItemDataRole.UserRole.
    # Named here so that refresh() (writer) and _delete_selected() (reader)
    # stay in sync automatically when columns are reorganised.
    _ID_COLUMN = 0

    def __init__(self, db: Database, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # Cache the manager — it is a lightweight DB wrapper; no need to
        # reconstruct it on every button click.
        self._mgr = CookieJarManager(db)
        self._init_ui()
        self.refresh()

    # ── UI construction ───────────────────────────────────────────────────────

    def _init_ui(self) -> None:
        layout = create_panel_layout(self)

        toolbar = QHBoxLayout()
        self.add_btn = QPushButton("Add…")
        self.delete_btn = QPushButton("Delete")
        self.clear_btn = QPushButton("Clear All")
        self.refresh_btn = QPushButton("Refresh")

        self.add_btn.clicked.connect(self._add_cookie)
        self.delete_btn.clicked.connect(self._delete_selected)
        self.clear_btn.clicked.connect(self._clear_all)
        self.refresh_btn.clicked.connect(self.refresh)
        self.reveal_btn = QCheckBox("Reveal Values")
        self.reveal_btn.setChecked(False)
        self.reveal_btn.toggled.connect(self.refresh)

        self.delete_btn.setEnabled(False)

        toolbar.addWidget(self.add_btn)
        toolbar.addWidget(self.delete_btn)
        toolbar.addWidget(self.clear_btn)
        toolbar.addWidget(self.reveal_btn)
        toolbar.addStretch()
        toolbar.addWidget(self.refresh_btn)
        layout.addLayout(toolbar)

        self.table = QTableWidget(0, len(self._COLUMNS))
        self.table.setHorizontalHeaderLabels(self._COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        layout.addWidget(self.table)

        self.count_label = create_muted_label()
        layout.addWidget(self.count_label)

    # ── Public API ────────────────────────────────────────────────────────────

    def refresh(self) -> None:
        """Reload the cookie list from the database and repopulate the table."""
        try:
            cookies = self._mgr.list_cookies()
        except Exception as exc:
            logger.error("Failed to load cookies: %s", exc, exc_info=True)
            self.count_label.setText("Failed to load cookies — see log for details")
            return

        # Disable sorting and painting during bulk insert to avoid O(n²)
        # sort passes and visual flicker on every insertRow() call.
        self.table.setSortingEnabled(False)
        self.table.setUpdatesEnabled(False)
        try:
            self.table.setRowCount(0)
            for cookie in cookies:
                row = self.table.rowCount()
                self.table.insertRow(row)

                name_item = QTableWidgetItem(cookie["name"])
                # Attach the DB id to the name cell for later retrieval.
                name_item.setData(Qt.ItemDataRole.UserRole, cookie["id"])

                self.table.setItem(row, self._ID_COLUMN, name_item)
                value = cookie["value"]
                value_item = QTableWidgetItem(value if self.reveal_btn.isChecked() else "••••••••")
                value_item.setToolTip(value)
                self.table.setItem(row, 1, value_item)
                self.table.setItem(row, 2, QTableWidgetItem(cookie.get("domain", "")))
                self.table.setItem(row, 3, QTableWidgetItem(cookie.get("path", "/")))
                self.table.setItem(
                    row, 4, QTableWidgetItem("Yes" if cookie.get("secure") else "No")
                )
        finally:
            self.table.setUpdatesEnabled(True)
            self.table.setSortingEnabled(True)

        count = len(cookies)
        self.count_label.setText(f"{count} cookie{'s' if count != 1 else ''}")

    def _show_context_menu(self, position) -> None:
        item = self.table.itemAt(position)
        if item is None:
            return
        row = item.row()
        menu = QMenu(self)
        copy_name = menu.addAction("Copy Name")
        copy_value = menu.addAction("Copy Value")
        action = menu.exec(self.table.viewport().mapToGlobal(position))
        clipboard = QApplication.clipboard()
        if clipboard is None:
            return
        if action == copy_name:
            name_item = self.table.item(row, 0)
            if name_item is not None:
                clipboard.setText(name_item.text())
        elif action == copy_value:
            value_item = self.table.item(row, 1)
            if value_item is not None:
                clipboard.setText(value_item.toolTip() or value_item.text())

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_selection_changed(self) -> None:
        self.delete_btn.setEnabled(bool(self.table.selectedItems()))

    def _add_cookie(self) -> None:
        dialog = _AddCookieDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            vals = dialog.values()
        except ValidationError as exc:
            QMessageBox.warning(self, "Validation", str(exc))
            return
        logger.debug("Adding cookie: name=%r domain=%r", vals["name"], vals["domain"])
        try:
            self._mgr.add_cookie(**vals)
        except Exception as exc:
            logger.error("Failed to add cookie %r: %s", vals["name"], exc, exc_info=True)
            QMessageBox.warning(self, "Error", str(exc))
            return
        self.refresh()

    def _delete_selected(self) -> None:
        # selectionModel().selectedRows() returns one index per selected row —
        # the correct API for a SelectRows table (selectedIndexes() returns one
        # index per *cell*, which requires an extra deduplication step).
        selected = self.table.selectionModel().selectedRows()
        if not selected:
            return

        count = len(selected)
        if not confirm_yes_no(
            self,
            "Confirm Delete",
            f"Delete {count} selected cookie{'s' if count != 1 else ''}?",
        ):
            return

        errors: list[str] = []
        for index in selected:
            item = self.table.item(index.row(), self._ID_COLUMN)
            if item is None:
                continue
            cookie_id = item.data(Qt.ItemDataRole.UserRole)
            try:
                self._mgr.delete_cookie(cookie_id)
            except Exception as exc:
                logger.error("Failed to delete cookie id=%s: %s", cookie_id, exc, exc_info=True)
                errors.append(str(exc))

        # Always refresh so the table reflects the actual DB state, even on
        # partial failure.
        self.refresh()

        if errors:
            QMessageBox.warning(
                self,
                "Delete Errors",
                f"{len(errors)} deletion(s) failed:\n\n" + "\n".join(errors),
            )

    def _clear_all(self) -> None:
        if not confirm_yes_no(self, "Confirm Clear", "Clear all cookies?"):
            return
        try:
            self._mgr.clear_cookies()
        except Exception as exc:
            logger.error("Failed to clear cookies: %s", exc, exc_info=True)
            QMessageBox.warning(self, "Error", str(exc))
            return
        self.refresh()

    # ── Private helpers ───────────────────────────────────────────────────────

    # Confirmation logic is shared via ``ui_common.confirm_yes_no``.
