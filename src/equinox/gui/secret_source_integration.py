"""Integration between secret managers and saved credentials GUI.

Provides extensions to the saved credentials dialog to use secrets from managers.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QComboBox,
    QMessageBox,
    QGroupBox,
    QFormLayout,
)

from equinox.core.secret_managers import list_available_managers

logger = logging.getLogger(__name__)


class SecretSourceConfigWidget(QGroupBox):
    """Widget for configuring a secret source for credentials.

    Allows users to specify that a credential should pull its values
    from an external secret manager instead of storing them locally.
    """

    def __init__(self, parent=None):
        """Initialize the widget.

        Args:
            parent: Parent widget
        """
        super().__init__("Secret Source (Optional)", parent)
        self._init_ui()

    def _init_ui(self) -> None:
        """Initialize the UI."""
        layout = QFormLayout(self)

        # Enable/disable secret source
        self.enable_check = QPushButton("Enable Secret Source")
        self.enable_check.setCheckable(True)
        self.enable_check.toggled.connect(self._on_enable_toggled)
        layout.addRow("", self.enable_check)

        # Manager type
        manager_label = QLabel("Manager Type:")
        self.manager_combo = QComboBox()
        self.manager_combo.addItems(list_available_managers())
        self.manager_combo.setVisible(False)
        manager_label.setVisible(False)
        layout.addRow(manager_label, self.manager_combo)
        self._manager_label = manager_label
        self._manager_combo_label = manager_label

        # Secret identifier
        secret_label = QLabel("Secret ID/Path:")
        self.secret_input = QLineEdit()
        self.secret_input.setPlaceholderText(
            "e.g., my-secret, secret/data/db-creds, or UUID"
        )
        self.secret_input.setVisible(False)
        secret_label.setVisible(False)
        layout.addRow(secret_label, self.secret_input)
        self._secret_label = secret_label
        self._secret_input_label = secret_label

        # JSON keys (for structured secrets)
        keys_label = QLabel("JSON Keys:")
        self.keys_input = QLineEdit()
        self.keys_input.setPlaceholderText("e.g., username,password (leave empty to use whole secret)")
        self.keys_input.setVisible(False)
        keys_label.setVisible(False)
        layout.addRow(keys_label, self.keys_input)
        self._keys_label = keys_label
        self._keys_input_label = keys_label

        # Info text
        info_label = QLabel(
            "When enabled, the credential will fetch values from the secret manager "
            "instead of storing them locally. This improves security by centralizing "
            "secret management."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: #666; font-size: 10px;")
        self.info_label = info_label
        layout.addRow("", info_label)

    def _on_enable_toggled(self, checked: bool) -> None:
        """Handle enable/disable toggle.

        Args:
            checked: Whether secret source is enabled
        """
        self.manager_combo.setVisible(checked)
        self._manager_label.setVisible(checked)
        self.secret_input.setVisible(checked)
        self._secret_label.setVisible(checked)
        self.keys_input.setVisible(checked)
        self._keys_label.setVisible(checked)

    def is_enabled(self) -> bool:
        """Check if secret source is enabled.

        Returns:
            True if enabled
        """
        return self.enable_check.isChecked()

    def get_config(self) -> Optional[Dict[str, Any]]:
        """Get the secret source configuration.

        Returns:
            Configuration dict if enabled, None otherwise
        """
        if not self.is_enabled():
            return None

        config: Dict[str, Any] = {
            "secret_source_type": self.manager_combo.currentText(),
            "secret_source_config": {
                "secret_name": self.secret_input.text().strip(),
            }
        }

        keys_str = self.keys_input.text().strip()
        if keys_str:
            config["secret_source_config"]["json_keys"] = [
                k.strip() for k in keys_str.split(",")
            ]

        return config

    def set_config(self, config: Dict[str, Any]) -> None:
        """Set the secret source configuration.

        Args:
            config: Configuration dictionary
        """
        source_type = config.get("secret_source_type")
        source_config = config.get("secret_source_config", {})

        if source_type:
            self.enable_check.setChecked(True)

            # Set manager type
            index = self.manager_combo.findText(source_type)
            if index >= 0:
                self.manager_combo.setCurrentIndex(index)

            # Set secret ID/path
            secret_name = source_config.get("secret_name", "")
            self.secret_input.setText(secret_name)

            # Set JSON keys
            json_keys = source_config.get("json_keys", [])
            if json_keys:
                self.keys_input.setText(",".join(json_keys))


class SecretSourceIntegration:
    """Helper class for integrating secret sources into credential dialogs."""

    @staticmethod
    def add_secret_source_widget(dialog: QDialog) -> SecretSourceConfigWidget:
        """Add a secret source configuration widget to a credential dialog.

        Args:
            dialog: The credential configuration dialog

        Returns:
            The secret source widget
        """
        widget = SecretSourceConfigWidget(dialog)

        # Find the main layout and add the widget
        if hasattr(dialog, "layout") and dialog.layout():
            layout = dialog.layout()
            # Add before buttons if possible
            widget_count = layout.count()
            if widget_count > 0:
                layout.insertWidget(widget_count - 1, widget)
            else:
                layout.addWidget(widget)

        return widget

    @staticmethod
    def apply_secret_source_to_config(
        config: Dict[str, Any],
        secret_widget: SecretSourceConfigWidget
    ) -> Dict[str, Any]:
        """Apply secret source configuration to a credential config.

        Args:
            config: Credential configuration
            secret_widget: Secret source widget

        Returns:
            Updated configuration
        """
        secret_source = secret_widget.get_config()
        if secret_source:
            config.update(secret_source)
        return config

    @staticmethod
    def validate_secret_source(config: Dict[str, Any]) -> tuple[bool, str]:
        """Validate secret source configuration.

        Args:
            config: Configuration to validate

        Returns:
            Tuple of (is_valid, error_message)
        """
        if "secret_source_type" not in config:
            return True, ""  # Secret source is optional

        source_type = config.get("secret_source_type")
        source_config = config.get("secret_source_config", {})

        if not source_type:
            return False, "Secret source type must be specified"

        if not source_config.get("secret_name"):
            return False, "Secret name/ID must be specified"

        # Validate manager type
        if source_type not in list_available_managers():
            return False, f"Unknown manager type: {source_type}"

        return True, ""

