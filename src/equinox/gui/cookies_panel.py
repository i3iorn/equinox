"""Cookie Jar panel — view and manage persistent cookies."""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QTableWidget, QTableWidgetItem, QMessageBox, QHeaderView,
    QDialog, QFormLayout, QLineEdit, QCheckBox, QDialogButtonBox,
)
from PyQt6.QtCore import Qt

from equinox.storage.cookies import CookieJarManager
from equinox.storage import Database


class _AddCookieDialog(QDialog):
    """Simple dialog to add a cookie manually."""

    def __init__(self, parent=None):
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

        form.addRow("Name:", self.name_edit)
        form.addRow("Value:", self.value_edit)
        form.addRow("Domain:", self.domain_edit)
        form.addRow("Path:", self.path_edit)
        form.addRow("Secure:", self.secure_cb)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout.addLayout(form)
        layout.addWidget(buttons)

    def values(self):
        return {
            "name":   self.name_edit.text().strip(),
            "value":  self.value_edit.text(),
            "domain": self.domain_edit.text().strip(),
            "path":   self.path_edit.text().strip() or "/",
            "secure": self.secure_cb.isChecked(),
        }


class CookiesPanel(QWidget):
    """Left-panel tab for managing the persistent cookie jar."""

    _COLUMNS = ("Name", "Value", "Domain", "Path", "Secure")

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db = db
        self._init_ui()
        self.refresh()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        toolbar = QHBoxLayout()
        self.add_btn    = QPushButton("Add…")
        self.delete_btn = QPushButton("Delete")
        self.clear_btn  = QPushButton("Clear All")
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
        self.table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self.table)

        self.count_label = QLabel()
        self.count_label.setObjectName("mutedLabel")
        layout.addWidget(self.count_label)

    def refresh(self):
        try:
            mgr = CookieJarManager(self.db)
            cookies = mgr.list_cookies()
        except Exception:
            return

        self.table.setRowCount(0)
        for cookie in cookies:
            row = self.table.rowCount()
            self.table.insertRow(row)

            self.table.setItem(row, 0, QTableWidgetItem(cookie["name"]))
            self.table.setItem(row, 1, QTableWidgetItem(cookie["value"]))
            self.table.setItem(row, 2, QTableWidgetItem(cookie.get("domain", "")))
            self.table.setItem(row, 3, QTableWidgetItem(cookie.get("path", "/")))
            self.table.setItem(row, 4, QTableWidgetItem(
                "Yes" if cookie.get("secure") else "No"
            ))

            # Store DB id in the first cell's user role
            self.table.item(row, 0).setData(Qt.ItemDataRole.UserRole, cookie["id"])

        count = len(cookies)
        self.count_label.setText(f"{count} cookie{'s' if count != 1 else ''}")

    def _on_selection_changed(self):
        self.delete_btn.setEnabled(bool(self.table.selectedItems()))

    def _add_cookie(self):
        dialog = _AddCookieDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        vals = dialog.values()
        if not vals["name"]:
            return
        try:
            mgr = CookieJarManager(self.db)
            mgr.add_cookie(**vals)
        except Exception as exc:
            QMessageBox.warning(self, "Error", str(exc))
            return
        self.refresh()

    def _delete_selected(self):
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
        try:
            mgr = CookieJarManager(self.db)
            for row in rows:
                cookie_id = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
                mgr.delete_cookie(cookie_id)
        except Exception as exc:
            QMessageBox.warning(self, "Error", str(exc))
        self.refresh()

    def _clear_all(self):
        reply = QMessageBox.question(
            self, "Confirm Clear",
            "Clear all cookies?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            mgr = CookieJarManager(self.db)
            mgr.clear_cookies()
        except Exception:
            pass
        self.refresh()
