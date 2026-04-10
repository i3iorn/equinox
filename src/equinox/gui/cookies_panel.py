"""Cookie Jar panel — view and manage persistent cookies."""
from __future__ import annotations

import logging

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QTableWidget, QTableWidgetItem, QMessageBox, QHeaderView,
    QDialog, QFormLayout, QLineEdit, QCheckBox, QDialogButtonBox,
)
from PyQt6.QtCore import Qt

from equinox.storage.cookies import CookieJarManager
from equinox.storage import Database

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

        self.name_edit   = QLineEdit()
        self.value_edit  = QLineEdit()
        self.domain_edit = QLineEdit()
        self.path_edit   = QLineEdit("/")
        self.secure_cb   = QCheckBox()

        form.addRow("Name:",   self.name_edit)
        form.addRow("Value:",  self.value_edit)
        form.addRow("Domain:", self.domain_edit)
        form.addRow("Path:",   self.path_edit)
        form.addRow("Secure:", self.secure_cb)
        layout.addLayout(form)

        self._error_label = QLabel()
        self._error_label.setStyleSheet("color: #f38ba8;")  # error red
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
            "name":   self.name_edit.text().strip(),
            "value":  self.value_edit.text(),
            "domain": self.domain_edit.text().strip(),
            "path":   self.path_edit.text().strip() or "/",
            "secure": self.secure_cb.isChecked(),
        }


# ── Main panel ────────────────────────────────────────────────────────────────


class CookiesPanel(QWidget):
    """Left-panel tab for managing the persistent cookie jar."""

    _COLUMNS = ("Name", "Value", "Domain", "Path", "Secure")

    def __init__(self, db: Database, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # Cache the manager — it is a lightweight DB wrapper; no need to
        # reconstruct it on every button click.
        self._mgr = CookieJarManager(db)
        self._init_ui()
        self.refresh()

    # ── UI construction ───────────────────────────────────────────────────────

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        toolbar = QHBoxLayout()
        self.add_btn     = QPushButton("Add…")
        self.delete_btn  = QPushButton("Delete")
        self.clear_btn   = QPushButton("Clear All")
        self.refresh_btn = QPushButton("Refresh")

        self.add_btn.clicked.connect(self._add_cookie)
        self.delete_btn.clicked.connect(self._delete_selected)
        self.clear_btn.clicked.connect(self._clear_all)
        self.refresh_btn.clicked.connect(self.refresh)

        self.delete_btn.setEnabled(False)

        toolbar.addWidget(self.add_btn)
        toolbar.addWidget(self.delete_btn)
        toolbar.addWidget(self.clear_btn)
        toolbar.addStretch()
        toolbar.addWidget(self.refresh_btn)
        layout.addLayout(toolbar)

        self.table = QTableWidget(0, len(self._COLUMNS))
        self.table.setHorizontalHeaderLabels(self._COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self.table)

        self.count_label = QLabel()
        self.count_label.setObjectName("mutedLabel")
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

                self.table.setItem(row, 0, name_item)
                self.table.setItem(row, 1, QTableWidgetItem(cookie["value"]))
                self.table.setItem(row, 2, QTableWidgetItem(cookie.get("domain", "")))
                self.table.setItem(row, 3, QTableWidgetItem(cookie.get("path", "/")))
                self.table.setItem(row, 4, QTableWidgetItem(
                    "Yes" if cookie.get("secure") else "No"
                ))
        finally:
            self.table.setUpdatesEnabled(True)
            self.table.setSortingEnabled(True)

        count = len(cookies)
        self.count_label.setText(f"{count} cookie{'s' if count != 1 else ''}")

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_selection_changed(self) -> None:
        self.delete_btn.setEnabled(bool(self.table.selectedItems()))

    def _add_cookie(self) -> None:
        dialog = _AddCookieDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        vals = dialog.values()
        logger.debug("Adding cookie: name=%r domain=%r", vals["name"], vals["domain"])
        try:
            self._mgr.add_cookie(**vals)
        except Exception as exc:
            logger.error("Failed to add cookie %r: %s", vals["name"], exc, exc_info=True)
            QMessageBox.warning(self, "Error", str(exc))
            return
        self.refresh()

    def _delete_selected(self) -> None:
        rows = {idx.row() for idx in self.table.selectedIndexes()}
        if not rows:
            return
        reply = QMessageBox.question(
            self, "Confirm Delete",
            f"Delete {len(rows)} selected cookie(s)?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        errors: list[str] = []
        for row in rows:
            cookie_id = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
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
                self, "Delete Errors",
                f"{len(errors)} deletion(s) failed:\n\n" + "\n".join(errors),
            )

    def _clear_all(self) -> None:
        reply = QMessageBox.question(
            self, "Confirm Clear",
            "Clear all cookies?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            self._mgr.clear_cookies()
        except Exception as exc:
            logger.error("Failed to clear cookies: %s", exc, exc_info=True)
            QMessageBox.warning(self, "Error", str(exc))
            return
        self.refresh()
