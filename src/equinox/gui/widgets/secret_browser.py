"""Widget for browsing and selecting secrets from a secret manager."""

from __future__ import annotations

import logging
from typing import Any

from PyQt6.QtCore import QObject, Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from equinox.core.secret_managers import (
    SecretManagerError,
    SecretManagerProfile,
    SecretNotFoundError,
)

logger = logging.getLogger(__name__)


class SecretRetrievalWorker(QObject):
    """Worker thread for retrieving secrets without blocking UI."""

    finished = pyqtSignal()
    error = pyqtSignal(str)
    secret_retrieved = pyqtSignal(str, dict)  # secret_name, secret_dict

    def __init__(
        self,
        manager_type: str,
        config: dict[str, Any],
        enable_cache: bool,
        cache_ttl: int,
        secret_name: str,
    ):
        super().__init__()
        self.manager_type = manager_type
        self.config = config
        self.enable_cache = enable_cache
        self.cache_ttl = cache_ttl
        self.secret_name = secret_name

    def run(self) -> None:
        """Retrieve the secret."""
        try:
            mgr = SecretManagerProfile.from_manager_config(
                self.manager_type,
                self.config,
                enable_cache=self.enable_cache,
                cache_ttl=self.cache_ttl,
            ).get_manager()

            try:
                secret_dict = mgr.get_secret_dict(self.secret_name)
            except Exception:
                # Fallback to getting as string
                secret_str = mgr.get_secret(self.secret_name)
                secret_dict = {"value": secret_str}

            self.secret_retrieved.emit(self.secret_name, secret_dict)
        except SecretNotFoundError as exc:
            self.error.emit(f"Secret not found: {exc}")
        except SecretManagerError as exc:
            self.error.emit(f"Manager error: {exc}")
        except Exception as exc:
            self.error.emit(f"Error retrieving secret: {exc}")
        finally:
            self.finished.emit()


class SecretBrowserWidget(QWidget):
    """Widget for browsing and selecting secrets from a secret manager.

    Allows users to:
    - Enter or search for a secret name
    - Retrieve secret values
    - View secret structure (for JSON secrets)
    - Select and use secrets in credentials
    """

    # Signal emitted when a secret is selected
    secret_selected = pyqtSignal(str, dict)  # secret_name, secret_dict

    def __init__(
        self,
        manager_type: str,
        config: dict[str, Any],
        enable_cache: bool = True,
        cache_ttl: int = 300,
        parent=None,
    ):
        """Initialize the secret browser.

        Args:
            manager_type: Type of secret manager
            config: Manager configuration
            parent: Parent widget
        """
        super().__init__(parent)
        self.manager_type = manager_type
        self.config = config
        self.enable_cache = enable_cache
        self.cache_ttl = cache_ttl
        self._init_ui()
        self._retrieval_thread: QThread | None = None

    def _init_ui(self) -> None:
        """Initialize the UI."""
        layout = QVBoxLayout(self)

        # Search/input section
        search_layout = QHBoxLayout()
        search_layout.addWidget(QLabel("Secret Name/ID:"))

        self.secret_input = QLineEdit()
        self.secret_input.setPlaceholderText(
            "Enter secret name, path, or ID (e.g., db-password or secret/data/db)"
        )
        self.secret_input.returnPressed.connect(self._retrieve_secret)
        search_layout.addWidget(self.secret_input)

        retrieve_btn = QPushButton("Retrieve")
        retrieve_btn.clicked.connect(self._retrieve_secret)
        search_layout.addWidget(retrieve_btn)

        layout.addLayout(search_layout)

        # Progress bar
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        # Results display
        results_label = QLabel("Secret Fields:")
        layout.addWidget(results_label)

        self.results_list = QListWidget()
        self.results_list.itemDoubleClicked.connect(self._on_field_selected)
        layout.addWidget(self.results_list)

        # Selected secret display
        selection_layout = QHBoxLayout()
        selection_layout.addWidget(QLabel("Selected Field:"))

        self.selected_display = QLineEdit()
        self.selected_display.setReadOnly(True)
        selection_layout.addWidget(self.selected_display)

        copy_btn = QPushButton("Copy Value")
        copy_btn.clicked.connect(self._copy_selected)
        selection_layout.addWidget(copy_btn)

        layout.addLayout(selection_layout)

        # Use secret button
        use_btn = QPushButton("Use This Secret")
        use_btn.clicked.connect(self._use_secret)
        layout.addWidget(use_btn)

        self._current_secret: dict[str, Any] | None = None
        self._selected_key: str | None = None

    def _retrieve_secret(self) -> None:
        """Retrieve the secret from the manager."""
        secret_name = self.secret_input.text().strip()
        if not secret_name:
            QMessageBox.warning(self, "Input", "Please enter a secret name or ID")
            return

        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.results_list.clear()
        self.selected_display.clear()
        self._current_secret = None
        self._selected_key = None

        # Create worker thread
        worker = SecretRetrievalWorker(
            self.manager_type, self.config, self.enable_cache, self.cache_ttl, secret_name
        )

        self._retrieval_thread = QThread()
        worker.moveToThread(self._retrieval_thread)
        worker.finished.connect(self._on_retrieval_finished)
        worker.error.connect(self._on_retrieval_error)
        worker.secret_retrieved.connect(self._on_secret_retrieved)

        self._retrieval_thread.started.connect(worker.run)
        self._retrieval_thread.start()

    def _on_secret_retrieved(self, secret_name: str, secret_dict: dict[str, Any]) -> None:
        """Handle successful secret retrieval.

        Args:
            secret_name: Name of the retrieved secret
            secret_dict: Dictionary of secret fields
        """
        self._current_secret = secret_dict
        self.results_list.clear()

        # Populate results list
        for key, value in secret_dict.items():
            display_value = str(value)
            if len(display_value) > 50:
                display_value = display_value[:47] + "..."

            item = QListWidgetItem(f"{key}: {display_value}")
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.results_list.addItem(item)

        logger.debug("Retrieved secret: %s (%d fields)", secret_name, len(secret_dict))

    def _on_retrieval_error(self, error_msg: str) -> None:
        """Handle retrieval error.

        Args:
            error_msg: Error message
        """
        QMessageBox.critical(self, "Retrieval Error", error_msg)
        logger.error("Failed to retrieve secret: %s", error_msg)

    def _on_retrieval_finished(self) -> None:
        """Handle retrieval completion."""
        self.progress.setVisible(False)
        if self._retrieval_thread:
            self._retrieval_thread.quit()
            self._retrieval_thread.wait()
            self._retrieval_thread = None

    def _on_field_selected(self, item: QListWidgetItem) -> None:
        """Handle field selection.

        Args:
            item: Selected list item
        """
        self._selected_key = item.data(Qt.ItemDataRole.UserRole)
        if self._current_secret and self._selected_key:
            value = self._current_secret.get(self._selected_key, "")
            self.selected_display.setText(str(value)[:100])

    def _copy_selected(self) -> None:
        """Copy the selected field value to clipboard."""
        if not self._current_secret or not self._selected_key:
            QMessageBox.warning(self, "Selection", "Please select a field first")
            return

        value = self._current_secret.get(self._selected_key, "")
        QApplication.clipboard().setText(str(value))
        QMessageBox.information(self, "Copied", "Value copied to clipboard")

    def _use_secret(self) -> None:
        """Emit signal to use the current secret."""
        if not self._current_secret:
            QMessageBox.warning(self, "Selection", "Please retrieve a secret first")
            return

        secret_name = self.secret_input.text().strip()
        self.secret_selected.emit(secret_name, self._current_secret)
        logger.debug("Secret selected: %s", secret_name)
