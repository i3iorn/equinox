"""Dialog wrapper for the secret-manager settings panel."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import QDialog, QVBoxLayout, QWidget


class SecretManagerSettingsDialog(QDialog):
    """Dedicated dialog host for secret-manager profile management."""

    def __init__(self, config_path: Path | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Secret Managers")
        self.setMinimumSize(600, 500)

        # Deferred: secret_manager_panel.py imports SecretManagerConfigDialog
        # from this package (equinox.gui.dialogs), so importing
        # SecretManagerSettingsPanel at module level here is circular
        # whenever equinox.gui.secret_manager_panel is the first of the two
        # to start loading — module-level import order isn't controlled by
        # this file, so defer to first-use instead.
        from equinox.gui.secret_manager_panel import SecretManagerSettingsPanel

        layout = QVBoxLayout(self)
        self.panel = SecretManagerSettingsPanel(config_path=config_path, parent=self)
        layout.addWidget(self.panel)
