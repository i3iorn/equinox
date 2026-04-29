"""Secret Manager settings and configuration panel."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QComboBox,
    QMessageBox,
    QGroupBox,
    QFormLayout,
    QTextEdit,
)

from equinox.core.secret_managers import list_available_managers
from equinox.gui.dialogs.secret_manager_config_dialog import SecretManagerConfigDialog
from equinox.gui.widgets.secret_browser import SecretBrowserWidget

logger = logging.getLogger(__name__)


class SecretManagerSettingsPanel(QWidget):
    """Panel for managing secret manager configuration and access.

    Provides UI for:
    - Configuring secret managers
    - Saving/loading configurations
    - Browsing secrets
    """

    # Signal emitted when a secret is selected for use in a credential
    secret_selected = pyqtSignal(str, dict)  # secret_name, secret_dict

    def __init__(self, config_path: Optional[Path] = None, parent=None):
        """Initialize the settings panel.

        Args:
            config_path: Path to store secret manager configurations
            parent: Parent widget
        """
        super().__init__(parent)
        self.config_path = config_path or (Path.home() / ".equinox" / "secret_managers.json")
        self.config_path.parent.mkdir(parents=True, exist_ok=True)

        self._current_config: Dict[str, Any] = {}
        self._browser_widget: Optional[SecretBrowserWidget] = None

        self._init_ui()
        self._load_configurations()

    def _init_ui(self) -> None:
        """Initialize the UI."""
        layout = QVBoxLayout(self)

        # Configuration selection
        config_layout = QHBoxLayout()
        config_layout.addWidget(QLabel("Saved Configuration:"))

        self.config_combo = QComboBox()
        self.config_combo.currentTextChanged.connect(self._on_config_selected)
        config_layout.addWidget(self.config_combo)

        new_btn = QPushButton("New")
        new_btn.clicked.connect(self._create_new_config)
        config_layout.addWidget(new_btn)

        delete_btn = QPushButton("Delete")
        delete_btn.clicked.connect(self._delete_current_config)
        config_layout.addWidget(delete_btn)

        layout.addLayout(config_layout)

        # Configuration display
        config_group = QGroupBox("Current Configuration")
        config_form = QFormLayout(config_group)

        self.config_display = QTextEdit()
        self.config_display.setReadOnly(True)
        self.config_display.setMaximumHeight(100)
        config_form.addRow("Details:", self.config_display)

        layout.addWidget(config_group)

        # Secret browser area
        browser_group = QGroupBox("Secret Browser")
        browser_layout = QVBoxLayout(browser_group)

        browser_info = QLabel(
            "Use the browser below to search for and retrieve secrets from the configured manager."
        )
        browser_layout.addWidget(browser_info)

        self.browser_placeholder = QLabel("No configuration selected")
        self.browser_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        browser_layout.addWidget(self.browser_placeholder)

        layout.addWidget(browser_group)

        # Status/buttons
        button_layout = QHBoxLayout()

        test_btn = QPushButton("Test Connection")
        test_btn.clicked.connect(self._test_connection)
        button_layout.addWidget(test_btn)

        clear_cache_btn = QPushButton("Clear Cache")
        clear_cache_btn.clicked.connect(self._clear_cache)
        button_layout.addWidget(clear_cache_btn)

        layout.addLayout(button_layout)
        layout.addStretch()

    def _load_configurations(self) -> None:
        """Load saved configurations from file."""
        if self.config_path.exists():
            try:
                with open(self.config_path, "r") as f:
                    configs = json.load(f)
                    self.config_combo.addItems(configs.keys())
                    logger.info("Loaded %d secret manager configurations", len(configs))
            except Exception as exc:
                logger.error("Failed to load configurations: %s", exc)

    def _save_configurations(self) -> None:
        """Save all configurations to file."""
        try:
            configs = {}
            for i in range(self.config_combo.count()):
                name = self.config_combo.itemText(i)
                # Retrieve config data (would need to store in combo data)
                # For now, just save what we have

            with open(self.config_path, "w") as f:
                json.dump(configs, f, indent=2)
            logger.debug("Saved secret manager configurations")
        except Exception as exc:
            logger.error("Failed to save configurations: %s", exc)
            QMessageBox.critical(self, "Save Error", f"Failed to save: {exc}")

    def _create_new_config(self) -> None:
        """Create a new secret manager configuration."""
        dialog = SecretManagerConfigDialog(self)
        dialog.config_saved.connect(self._on_config_created)
        dialog.exec()

    def _on_config_created(self, manager_type: str, config: Dict[str, Any]) -> None:
        """Handle configuration creation.

        Args:
            manager_type: Type of secret manager
            config: Configuration dictionary
        """
        # Prompt for a name
        from PyQt6.QtWidgets import QInputDialog

        name, ok = QInputDialog.getText(
            self,
            "Save Configuration",
            "Enter a name for this configuration:"
        )

        if not ok or not name:
            return

        # Store the configuration
        self._current_config = {
            "type": manager_type,
            "config": {k: v for k, v in config.items() if k not in ("enable_cache", "cache_ttl")},
            "enable_cache": config.get("enable_cache", True),
            "cache_ttl": config.get("cache_ttl", 300),
        }

        # Add to combo if not already there
        if name not in [self.config_combo.itemText(i) for i in range(self.config_combo.count())]:
            self.config_combo.addItem(name)

        self.config_combo.setCurrentText(name)
        self._display_config()
        self._create_browser()
        self._save_configurations()

        logger.info("Created new configuration: %s (%s)", name, manager_type)

    def _on_config_selected(self, name: str) -> None:
        """Handle configuration selection.

        Args:
            name: Configuration name
        """
        if not name:
            self.browser_placeholder.setText("No configuration selected")
            return

        # In a real implementation, load from saved configurations
        # For now, just display placeholder
        self._display_config()

    def _display_config(self) -> None:
        """Display current configuration details."""
        if not self._current_config:
            self.config_display.setText("No configuration loaded")
            return

        display_text = f"""
Manager Type: {self._current_config.get('type', 'N/A')}
Cache Enabled: {self._current_config.get('enable_cache', True)}
Cache TTL: {self._current_config.get('cache_ttl', 300)}s

Configuration:
{json.dumps(self._current_config.get('config', {}), indent=2)}
"""
        self.config_display.setText(display_text)

    def _create_browser(self) -> None:
        """Create and display the secret browser widget."""
        if not self._current_config:
            return

        # Remove old browser if exists
        parent = self.browser_placeholder.parent()
        layout = parent.layout()

        if self._browser_widget:
            layout.removeWidget(self._browser_widget)
            self._browser_widget.deleteLater()

        layout.removeWidget(self.browser_placeholder)
        self.browser_placeholder.setVisible(False)

        # Create new browser
        manager_type = self._current_config.get("type", "env")
        config = self._current_config.get("config", {})

        self._browser_widget = SecretBrowserWidget(manager_type, config, self)
        self._browser_widget.secret_selected.connect(self.secret_selected)
        layout.insertWidget(0, self._browser_widget)

    def _test_connection(self) -> None:
        """Test connection to the configured secret manager."""
        if not self._current_config:
            QMessageBox.warning(self, "Configuration", "No configuration selected")
            return

        from equinox.core.secret_managers import get_secret_manager

        try:
            manager_type = self._current_config.get("type", "env")
            config = self._current_config.get("config", {})

            mgr = get_secret_manager(manager_type)
            mgr.configure(**config)

            if mgr.is_available():
                QMessageBox.information(
                    self,
                    "Connection Successful",
                    f"Successfully connected to {manager_type} secret manager."
                )
            else:
                QMessageBox.warning(
                    self,
                    "Connection Failed",
                    f"Cannot connect to {manager_type} secret manager."
                )
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Error: {exc}")

    def _clear_cache(self) -> None:
        """Clear the secret manager cache."""
        if not self._current_config:
            return

        from equinox.core.secret_managers import get_secret_manager

        try:
            manager_type = self._current_config.get("type", "env")
            mgr = get_secret_manager(manager_type)
            mgr.clear_cache()

            QMessageBox.information(self, "Cache Cleared", "Secret cache has been cleared")
            logger.info("Cleared secret cache for %s", manager_type)
        except Exception as exc:
            logger.error("Failed to clear cache: %s", exc)

    def _delete_current_config(self) -> None:
        """Delete the currently selected configuration."""
        current_name = self.config_combo.currentText()
        if not current_name:
            return

        reply = QMessageBox.question(
            self,
            "Confirm Delete",
            f"Delete configuration '{current_name}'?"
        )

        if reply != QMessageBox.StandardButton.Yes:
            return

        self.config_combo.removeItem(self.config_combo.currentIndex())
        self._current_config = {}
        self._save_configurations()
        logger.info("Deleted configuration: %s", current_name)

