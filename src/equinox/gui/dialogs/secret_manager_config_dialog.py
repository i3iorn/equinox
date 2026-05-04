"""Secret manager configuration dialog and widgets.

Provides PyQt6 UI components for configuring and managing secret managers.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QMessageBox,
    QSpinBox,
    QCheckBox,
)

from equinox.core.secret_managers import (
    get_secret_manager,
    list_available_managers,
    SecretManagerError,
    SecretAuthError,
)

logger = logging.getLogger(__name__)


class SecretManagerConfigDialog(QDialog):
    """Dialog for configuring secret managers.

    Provides UI for selecting a secret manager type and entering
    backend-specific configuration parameters.
    """

    # Signal emitted when configuration is successfully saved
    config_saved = pyqtSignal(str, dict)  # manager_type, config_dict
    _VAULT_MANAGER_TYPES = ("vault", "hashicorp_vault")

    def __init__(self, parent=None):
        """Initialize the configuration dialog.

        Args:
            parent: Parent widget
        """
        super().__init__(parent)
        self.setWindowTitle("Configure Secret Manager")
        self.setMinimumWidth(500)
        self.setMinimumHeight(300)

        self._config_widgets: Dict[str, list] = {}
        self._vault_warning_label: Optional[QLabel] = None
        self._init_ui()

    def _init_ui(self) -> None:
        """Initialize the UI."""
        layout = QVBoxLayout(self)

        # Manager type selection
        type_layout = QHBoxLayout()
        type_layout.addWidget(QLabel("Secret Manager Type:"))

        self.type_combo = QComboBox()
        self.type_combo.addItems(list_available_managers())
        self.type_combo.currentTextChanged.connect(self._on_manager_type_changed)
        type_layout.addWidget(self.type_combo)
        layout.addLayout(type_layout)

        # Configuration form (will be populated based on type)
        self.config_group = QGroupBox("Configuration")
        self.config_layout = QFormLayout(self.config_group)
        layout.addWidget(self.config_group)

        # Cache settings
        cache_group = QGroupBox("Cache Settings")
        cache_layout = QFormLayout(cache_group)

        self.enable_cache_check = QCheckBox("Enable caching")
        self.enable_cache_check.setChecked(True)
        cache_layout.addRow("Caching:", self.enable_cache_check)

        self.cache_ttl_spin = QSpinBox()
        self.cache_ttl_spin.setMinimum(0)
        self.cache_ttl_spin.setMaximum(3600)
        self.cache_ttl_spin.setValue(300)
        self.cache_ttl_spin.setSuffix(" seconds")
        cache_layout.addRow("TTL:", self.cache_ttl_spin)

        layout.addWidget(cache_group)

        # Buttons
        button_layout = QHBoxLayout()

        self.test_btn = QPushButton("Test Connection")
        self.test_btn.clicked.connect(self._test_connection)
        button_layout.addWidget(self.test_btn)

        button_layout.addStretch()

        save_btn = QPushButton("Save Configuration")
        save_btn.clicked.connect(self._save_configuration)
        button_layout.addWidget(save_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)

        layout.addLayout(button_layout)

        # Initialize configuration fields for default manager type
        self._on_manager_type_changed(self.type_combo.currentText())

    def _on_manager_type_changed(self, manager_type: str) -> None:
        """Handle manager type selection change.

        Args:
            manager_type: Selected manager type
        """
        # Clear existing config widgets
        self._vault_warning_label = None
        while self.config_layout.rowCount() > 0:
            self.config_layout.removeRow(0)
        self._config_widgets.clear()

        # Add configuration fields based on manager type
        if manager_type in ("env", "environment"):
            self._add_env_config_fields()
        elif manager_type in ("aws", "aws_secrets_manager"):
            self._add_aws_config_fields()
        elif manager_type in ("vault", "hashicorp_vault"):
            self._add_vault_config_fields()
        elif manager_type in ("bitwarden", "bw"):
            self._add_bitwarden_config_fields()

    def _add_env_config_fields(self) -> None:
        """Add configuration fields for environment variable manager."""
        prefix_input = QLineEdit()
        prefix_input.setText("EQUINOX_SECRET_")
        prefix_input.setToolTip("Prefix for environment variable names")
        self.config_layout.addRow("Prefix:", prefix_input)
        self._config_widgets["prefix"] = [prefix_input]

    def _add_aws_config_fields(self) -> None:
        """Add configuration fields for AWS Secrets Manager."""
        region_input = QLineEdit()
        region_input.setText("us-east-1")
        region_input.setToolTip("AWS region (e.g., us-east-1, eu-west-1)")
        self.config_layout.addRow("Region:", region_input)
        self._config_widgets["region_name"] = [region_input]

    def _add_vault_config_fields(self) -> None:
        """Add configuration fields for Vault."""
        url_input = QLineEdit()
        url_input.setText("https://vault.example.com:8200")
        url_input.setToolTip("Vault server URL")
        self.config_layout.addRow("URL:", url_input)
        self._config_widgets["url"] = [url_input]

        token_input = QLineEdit()
        token_input.setEchoMode(QLineEdit.EchoMode.Password)
        token_input.setToolTip("Vault authentication token (hvs.xxx)")
        self.config_layout.addRow("Token:", token_input)
        self._config_widgets["token"] = [token_input]

        verify_check = QCheckBox("Verify SSL")
        verify_check.setChecked(True)
        self.config_layout.addRow("SSL:", verify_check)
        self._config_widgets["verify_ssl"] = [verify_check]

        allow_insecure_check = QCheckBox("Allow insecure HTTP for local testing only")
        allow_insecure_check.setChecked(False)
        allow_insecure_check.setToolTip(
            "Only enable this when connecting to a trusted local/dev Vault instance over HTTP."
        )
        self.config_layout.addRow("HTTP Override:", allow_insecure_check)
        self._config_widgets["allow_insecure_http"] = [allow_insecure_check]

        warning_label = QLabel()
        warning_label.setWordWrap(True)
        warning_label.setStyleSheet("color: #c0392b; font-weight: bold;")
        warning_label.setVisible(False)
        self.config_layout.addRow("", warning_label)
        self._vault_warning_label = warning_label

        url_input.textChanged.connect(self._update_vault_security_warning)
        allow_insecure_check.toggled.connect(self._update_vault_security_warning)
        self._update_vault_security_warning()

    def _update_vault_security_warning(self) -> None:
        """Refresh the Vault security warning for insecure HTTP override usage."""
        label = self._vault_warning_label
        if label is None:
            return

        url_widget = self._config_widgets.get("url", [None])[0]
        allow_widget = self._config_widgets.get("allow_insecure_http", [None])[0]
        url = url_widget.text().strip() if isinstance(url_widget, QLineEdit) else ""
        allow_insecure = bool(allow_widget.isChecked()) if isinstance(allow_widget, QCheckBox) else False

        if url.lower().startswith("http://") and allow_insecure:
            label.setText(
                "Warning: insecure Vault HTTP override is enabled. Tokens and secrets may be exposed "
                "to interception. Use only for trusted local development."
            )
            label.setVisible(True)
            return

        if url.lower().startswith("http://"):
            label.setText(
                "Vault secret manager connections require HTTPS by default. This HTTP URL will be rejected "
                "unless you explicitly enable the local-testing override."
            )
            label.setVisible(True)
            return

        if allow_insecure:
            label.setText(
                "Warning: insecure Vault HTTP override is enabled but not currently needed because the URL "
                "uses HTTPS. Consider disabling the override."
            )
            label.setVisible(True)
            return

        label.clear()
        label.setVisible(False)

    def _confirm_insecure_vault_http(self, manager_type: str, config: Dict[str, Any], action: str) -> bool:
        """Warn before testing or saving an insecure Vault HTTP configuration."""
        if manager_type not in self._VAULT_MANAGER_TYPES:
            return True
        if not bool(config.get("allow_insecure_http")):
            return True
        url = str(config.get("url") or "").strip().lower()
        if not url.startswith("http://"):
            return True

        reply = QMessageBox.warning(
            self,
            "Insecure Vault HTTP Override",
            "You are allowing Vault over insecure HTTP. This may expose tokens and secrets on the network.\n\n"
            f"Continue and {action.lower()} this configuration?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return reply == QMessageBox.StandardButton.Yes

    def _add_bitwarden_config_fields(self) -> None:
        """Add configuration fields for Bitwarden."""
        org_input = QLineEdit()
        org_input.setToolTip("Organization ID (optional)")
        org_input.setPlaceholderText("Leave empty for personal vault")
        self.config_layout.addRow("Organization ID:", org_input)
        self._config_widgets["organization_id"] = [org_input]

        info_label = QLabel(
            "Ensure Bitwarden CLI is installed and you are logged in:\n"
            "  bw login your-email@example.com\n"
            "  bw unlock your-password"
        )
        self.config_layout.addRow("", info_label)

    def _get_config_dict(self) -> Dict[str, Any]:
        """Build configuration dictionary from UI fields.

        Returns:
            Configuration dictionary
        """
        config = {}

        for key, widgets in self._config_widgets.items():
            if not widgets:
                continue

            widget = widgets[0]

            if isinstance(widget, QLineEdit):
                value = widget.text().strip()
                if value:  # Only include non-empty values
                    config[key] = value
            elif isinstance(widget, QCheckBox):
                config[key] = widget.isChecked()
            elif isinstance(widget, QSpinBox):
                config[key] = widget.value()

        return config

    def _test_connection(self) -> None:
        """Test the connection to the configured secret manager."""
        manager_type = self.type_combo.currentText()
        config = self._get_config_dict()

        if not self._confirm_insecure_vault_http(manager_type, config, "Test"):
            return

        try:
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
                    f"Unable to connect to {manager_type} secret manager."
                )
        except SecretAuthError as exc:
            QMessageBox.critical(
                self,
                "Authentication Error",
                f"Authentication failed: {exc}"
            )
        except SecretManagerError as exc:
            QMessageBox.critical(
                self,
                "Configuration Error",
                f"Configuration error: {exc}"
            )
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Error",
                f"Error: {exc}"
            )

    def _save_configuration(self) -> None:
        """Save the configuration and emit signal."""
        manager_type = self.type_combo.currentText()
        config = self._get_config_dict()

        # Add cache settings to config
        config["enable_cache"] = self.enable_cache_check.isChecked()
        config["cache_ttl"] = self.cache_ttl_spin.value()

        # Validate required fields
        if not manager_type:
            QMessageBox.warning(self, "Validation", "Please select a manager type")
            return

        if not self._confirm_insecure_vault_http(manager_type, config, "Save"):
            return

        logger.info("Saving secret manager configuration: %s", manager_type)
        self.config_saved.emit(manager_type, config)
        self.accept()

