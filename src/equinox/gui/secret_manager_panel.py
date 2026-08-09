"""Secret Manager settings and configuration panel."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from equinox.core.json_tools import safe_json_dumps
from equinox.core.secret_managers import SecretManagerProfile
from equinox.core.secret_managers import test_secret_manager_connection
from equinox.gui.dialogs.secret_manager_config_dialog import SecretManagerConfigDialog
from equinox.gui.error_presenter import ErrorPresenter
from equinox.gui.secret_manager_feedback import SecretManagerConnectionMessages
from equinox.gui.secret_manager_feedback import show_secret_manager_connection_feedback
from equinox.gui.widgets.secret_browser import SecretBrowserWidget
from equinox.security import sanitize_details
from equinox.storage.secret_manager_configs import SecretManagerConfigStore
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QComboBox
from PyQt6.QtWidgets import QFormLayout
from PyQt6.QtWidgets import QGroupBox
from PyQt6.QtWidgets import QHBoxLayout
from PyQt6.QtWidgets import QLabel
from PyQt6.QtWidgets import QMessageBox
from PyQt6.QtWidgets import QPushButton
from PyQt6.QtWidgets import QTextEdit
from PyQt6.QtWidgets import QVBoxLayout
from PyQt6.QtWidgets import QWidget

logger = logging.getLogger(__name__)


_PANEL_CONNECTION_MESSAGES = SecretManagerConnectionMessages(
    success="Saved profile '{profile_name}' connected to {manager_type}.",
    unavailable="Saved profile '{profile_name}' could not reach {manager_type}.\n\n{error}",
    auth="Saved profile '{profile_name}' was rejected by {manager_type}.\n\n{error}",
    config="Saved profile '{profile_name}' is invalid for {manager_type}.\n\n{error}",
    unexpected="Unexpected error while testing saved profile '{profile_name}'.\n\n{error}",
)


class SecretManagerSettingsPanel(QWidget):
    """Panel for managing secret manager configuration and access.

    Provides UI for:
    - Configuring secret managers
    - Saving/loading configurations
    - Browsing secrets
    """

    # Signal emitted when a secret is selected for use in a credential
    secret_selected = pyqtSignal(str, dict)  # secret_name, secret_dict

    def __init__(self, config_path: Path | None = None, parent: QWidget | None = None) -> None:
        """Initialize the settings panel.

        Args:
            config_path: Path to store secret manager configurations
            parent: Parent widget
        """
        super().__init__(parent)
        self._config_store = SecretManagerConfigStore(config_path)

        self._current_config: dict[str, Any] = {}
        self._configs: dict[str, dict[str, Any]] = {}
        self._current_config_name: str = ""
        self._browser_widget: SecretBrowserWidget | None = None

        self._init_ui()
        self._load_configurations()

    def _init_ui(self) -> None:
        """Initialize the UI."""
        layout = QVBoxLayout(self)

        self._build_config_selector(layout)
        self._build_config_display(layout)
        self._build_browser_section(layout)
        self._build_action_buttons(layout)

        layout.addStretch()

    def _build_config_selector(self, parent_layout: QVBoxLayout) -> None:
        """Build the configuration selection row."""
        row = QHBoxLayout()
        row.addWidget(QLabel("Saved Configuration:"))

        self.config_combo = QComboBox()
        self.config_combo.currentTextChanged.connect(self._on_config_selected)
        row.addWidget(self.config_combo)

        new_btn = QPushButton("New")
        new_btn.clicked.connect(self._create_new_config)
        row.addWidget(new_btn)

        delete_btn = QPushButton("Delete")
        delete_btn.clicked.connect(self._delete_current_config)
        row.addWidget(delete_btn)

        parent_layout.addLayout(row)

    def _build_config_display(self, parent_layout: QVBoxLayout) -> None:
        """Build the configuration details display."""
        group = QGroupBox("Current Configuration")
        form = QFormLayout(group)

        self.config_display = QTextEdit()
        self.config_display.setReadOnly(True)
        self.config_display.setMaximumHeight(100)

        form.addRow("Details:", self.config_display)
        parent_layout.addWidget(group)

    def _build_browser_section(self, parent_layout: QVBoxLayout) -> None:
        """Build the secret browser container."""
        group = QGroupBox("Secret Browser")
        layout = QVBoxLayout(group)

        info = QLabel(
            "Use the browser below to search for and retrieve secrets from the configured manager.",
        )
        layout.addWidget(info)

        self.browser_placeholder = QLabel("No configuration selected")
        self.browser_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.browser_placeholder)

        parent_layout.addWidget(group)

    def _build_action_buttons(self, parent_layout: QVBoxLayout) -> None:
        """Build the bottom action buttons."""
        row = QHBoxLayout()

        test_btn = QPushButton("Test Connection")
        test_btn.clicked.connect(self._test_connection)
        row.addWidget(test_btn)

        clear_btn = QPushButton("Clear Cache")
        clear_btn.clicked.connect(self._clear_cache)
        row.addWidget(clear_btn)

        parent_layout.addLayout(row)

    def _load_configurations(self) -> None:
        """Load saved configurations from storage and populate the selector."""
        self._configs = self._config_store.load_all()
        self.config_combo.blockSignals(True)
        self.config_combo.clear()
        self.config_combo.addItems(sorted(self._configs.keys()))
        self.config_combo.blockSignals(False)
        logger.info("Loaded %d secret manager configurations", len(self._configs))
        if self.config_combo.count() > 0:
            self.config_combo.setCurrentIndex(0)
            self._on_config_selected(self.config_combo.currentText())

    def _save_configurations(self) -> None:
        """Persist in-memory configurations via storage layer."""
        try:
            self._config_store.save_all(self._configs)
            logger.debug("Saved secret manager configurations")
        except Exception as exc:
            logger.error("Failed to save configurations: %s", exc)
            ErrorPresenter.error(self, f"Failed to save: {exc}", title="Save Error")

    def _create_new_config(self) -> None:
        """Create a new secret manager configuration."""
        dialog = SecretManagerConfigDialog(self)
        dialog.config_saved.connect(self._on_config_created)
        dialog.exec()

    def _current_profile(self) -> SecretManagerProfile | None:
        """Return the selected secret-manager profile, if any."""
        if not self._current_config:
            return None
        return SecretManagerProfile.from_payload(self._current_config)

    def _on_config_created(self, manager_type: str, config: dict[str, Any]) -> None:
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
            "Enter a name for this configuration:",
        )

        if not ok or not name:
            return

        name = name.strip()
        if not name:
            return

        # Store the configuration
        payload = SecretManagerProfile.from_manager_config(
            manager_type,
            {k: v for k, v in config.items() if k not in ("enable_cache", "cache_ttl")},
            enable_cache=bool(config.get("enable_cache", True)),
            cache_ttl=int(config.get("cache_ttl", 300)),
        ).to_payload()
        self._configs[name] = payload
        self._current_config_name = name
        self._current_config = dict(payload)

        self.config_combo.blockSignals(True)
        if name not in [self.config_combo.itemText(i) for i in range(self.config_combo.count())]:
            self.config_combo.addItem(name)

        if self.config_combo is not None:
            model = self.config_combo.model()
            if model is not None:
                model.sort(0)
            self.config_combo.blockSignals(False)
            self.config_combo.setCurrentText(name)

        self._on_config_selected(name)
        self._display_config()
        self._save_configurations()

        logger.info("Created new configuration: %s (%s)", name, manager_type)

    def _on_config_selected(self, name: str) -> None:
        """Handle configuration selection.

        Args:
            name: Configuration name
        """
        if not name:
            self._current_config_name = ""
            self._current_config = {}
            self._remove_browser()
            self.config_display.setText("No configuration loaded")
            self.browser_placeholder.setText("No configuration selected")
            return

        cfg = self._configs.get(name)
        if cfg is None:
            self._current_config_name = ""
            self._current_config = {}
            self._remove_browser()
            self.config_display.setText("No configuration loaded")
            return

        self._current_config_name = name
        self._current_config = dict(cfg)
        self._display_config()
        self._create_browser()

    def _display_config(self) -> None:
        """Display current configuration details."""
        profile = self._current_profile()
        if profile is None:
            self.config_display.setText("No configuration loaded")
            return

        config = profile.config
        safe_config = sanitize_details(config)
        warning_text = ""
        if (
            profile.manager_type in ("vault", "hashicorp_vault")
            and config.get("allow_insecure_http")
            and str(config.get("url", "")).strip().lower().startswith("http://")
        ):
            warning_text = "\nWARNING: insecure Vault HTTP override is enabled. Use only for trusted local development.\n"

        display_text = f"""
Manager Type: {profile.manager_type}
Cache Enabled: {profile.enable_cache}
Cache TTL: {profile.cache_ttl}s
{warning_text}

Configuration:
{safe_json_dumps(safe_config, indent=2)}
"""
        self.config_display.setText(display_text)

    def _create_browser(self) -> None:
        """Create and display the secret browser widget."""
        profile = self._current_profile()
        if profile is None:
            return

        parent_widget = self.browser_placeholder.parentWidget()
        if parent_widget is None:
            return
        layout = parent_widget.layout()
        self._remove_browser()
        if layout is None:
            return
        layout.removeWidget(self.browser_placeholder)
        self.browser_placeholder.setVisible(False)

        # Create new browser
        self._browser_widget = SecretBrowserWidget(
            profile.manager_type,
            profile.config,
            enable_cache=profile.enable_cache,
            cache_ttl=profile.cache_ttl,
            parent=self,
        )
        self._browser_widget.secret_selected.connect(self.secret_selected)
        if hasattr(layout, "insertWidget"):
            layout.insertWidget(0, self._browser_widget)
        else:
            layout.addWidget(self._browser_widget)

    def _remove_browser(self) -> None:
        """Remove and destroy the current browser widget (if any)."""
        parent_widget = self.browser_placeholder.parentWidget()
        if parent_widget is None:
            return
        layout = parent_widget.layout()
        if layout is None:
            return
        if self._browser_widget:
            layout.removeWidget(self._browser_widget)
            self._browser_widget.deleteLater()
            self._browser_widget = None
        if layout.indexOf(self.browser_placeholder) == -1:
            layout.addWidget(self.browser_placeholder)
        self.browser_placeholder.setVisible(True)

    def _test_connection(self) -> None:
        """Test connection to the configured secret manager."""
        profile = self._current_profile()
        if profile is None:
            ErrorPresenter.warning(self, "No configuration selected", title="Configuration")
            return

        manager_type = profile.manager_type
        config = profile.to_payload()["config"]
        config["enable_cache"] = profile.enable_cache
        config["cache_ttl"] = profile.cache_ttl
        result = test_secret_manager_connection(manager_type, config)
        messages = SecretManagerConnectionMessages(
            success=_PANEL_CONNECTION_MESSAGES.success.format(
                profile_name=self._current_config_name,
                manager_type="{manager_type}",
            ),
            unavailable=_PANEL_CONNECTION_MESSAGES.unavailable.format(
                profile_name=self._current_config_name,
                manager_type="{manager_type}",
                error="{error}",
            ),
            auth=_PANEL_CONNECTION_MESSAGES.auth.format(
                profile_name=self._current_config_name,
                manager_type="{manager_type}",
                error="{error}",
            ),
            config=_PANEL_CONNECTION_MESSAGES.config.format(
                profile_name=self._current_config_name,
                manager_type="{manager_type}",
                error="{error}",
            ),
            unexpected=_PANEL_CONNECTION_MESSAGES.unexpected.format(
                profile_name=self._current_config_name,
                manager_type="{manager_type}",
                error="{error}",
            ),
        )
        show_secret_manager_connection_feedback(self, result, messages)

    def _clear_cache(self) -> None:
        """Clear the secret manager cache."""
        profile = self._current_profile()
        if profile is None:
            return

        try:
            manager_type = profile.manager_type
            mgr = profile.get_manager()
            mgr.clear_cache()

            ErrorPresenter.info(self, "Secret cache has been cleared", title="Cache Cleared")
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
            f"Delete configuration '{current_name}'?",
        )

        if reply != QMessageBox.StandardButton.Yes:
            return

        self._configs.pop(current_name, None)
        self.config_combo.removeItem(self.config_combo.currentIndex())
        self._current_config_name = ""
        self._current_config = {}
        self._remove_browser()
        self.config_display.setText("No configuration loaded")
        self._save_configurations()
        logger.info("Deleted configuration: %s", current_name)
