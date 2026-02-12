"""History panel"""

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QLabel,
    QMessageBox,
)
from PyQt6.QtCore import pyqtSignal, Qt

from equinox.storage import Database, HistoryManager


class HistoryPanel(QWidget):
    """Panel for viewing request history"""

    history_selected = pyqtSignal(int)  # History ID

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db = db
        self._init_ui()
        self.refresh()

    def _init_ui(self):
        """Initialize UI"""
        layout = QVBoxLayout(self)

        # Toolbar
        toolbar = QHBoxLayout()
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.refresh)
        self.clear_btn = QPushButton("Clear All")
        self.clear_btn.clicked.connect(self._clear_history)
        toolbar.addWidget(self.refresh_btn)
        toolbar.addWidget(self.clear_btn)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        # List widget
        self.list_widget = QListWidget()
        self.list_widget.itemDoubleClicked.connect(self._on_item_double_clicked)
        layout.addWidget(self.list_widget)

        # Stats label
        self.stats_label = QLabel()
        layout.addWidget(self.stats_label)

    def refresh(self):
        """Refresh history list"""
        self.list_widget.clear()

        mgr = HistoryManager(self.db)
        entries = mgr.list_history(limit=100)

        for entry in entries:
            # Format entry
            status = entry.get("status_code", "Error")
            method = entry["method"]
            url = entry["url"]
            executed_at = entry["executed_at"].split(".")[0]  # Remove microseconds

            text = f"[{status}] {method} {url}\n{executed_at}"

            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, entry["id"])

            # Color based on status
            if entry.get("error"):
                item.setForeground(Qt.GlobalColor.red)
            elif entry.get("status_code", 0) >= 400:
                item.setForeground(Qt.GlobalColor.red)
            else:
                item.setForeground(Qt.GlobalColor.darkGreen)

            self.list_widget.addItem(item)

        # Update stats
        stats = mgr.get_stats()
        self.stats_label.setText(
            f"Total: {stats['total']} | Success: {stats['successful']} | Failed: {stats['failed']}"
        )

    def _on_item_double_clicked(self, item: QListWidgetItem):
        """Handle item double click"""
        history_id = item.data(Qt.ItemDataRole.UserRole)
        if history_id:
            self.history_selected.emit(history_id)

    def _clear_history(self):
        """Clear all history"""
        reply = QMessageBox.question(
            self,
            "Confirm Clear",
            "Are you sure you want to clear all history?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            mgr = HistoryManager(self.db)
            mgr.clear_history()
            self.refresh()
