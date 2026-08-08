"""Dialog wrapper for the secret-manager settings panel."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import QDialog, QVBoxLayout, QWidget

from equinox.gui.secret_manager_panel import SecretManagerSettingsPanel


class SecretManagerSettingsDialog(QDialog):
    """Dedicated dialog host for secret-manager profile management."""

    def __init__(self, config_path: Path | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Secret Managers")
        self.setMinimumSize(600, 500)

        layout = QVBoxLayout(self)
        self.panel = SecretManagerSettingsPanel(config_path=config_path, parent=self)
        layout.addWidget(self.panel)
