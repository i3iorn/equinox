"""First-run setup wizard for essential UX preferences."""

from __future__ import annotations

from typing import Any

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from equinox.gui.theme import THEME_LABELS, THEME_MODES, get_theme_mode


class SetupWizardDialog(QDialog):
    """Collect first-run preferences and optional onboarding actions."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Welcome to Equinox")
        self.setMinimumWidth(460)

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Configure your initial workspace settings. "
            "You can change all of these later in Preferences.",
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        self._theme_combo = QComboBox()
        for mode in THEME_MODES:
            self._theme_combo.addItem(THEME_LABELS.get(mode, mode), mode)
        current_mode = get_theme_mode()
        idx = self._theme_combo.findData(current_mode)
        self._theme_combo.setCurrentIndex(idx if idx >= 0 else 0)
        form.addRow("Theme:", self._theme_combo)

        self._open_env_check = QCheckBox("Open environment manager after setup")
        self._open_env_check.setChecked(True)
        form.addRow("", self._open_env_check)

        self._open_creds_check = QCheckBox("Open saved credentials manager after setup")
        self._open_creds_check.setChecked(False)
        form.addRow("", self._open_creds_check)

        layout.addLayout(form)

        buttons = QDialogButtonBox()
        buttons.addButton(QDialogButtonBox.StandardButton.Ok)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def result_data(self) -> dict[str, Any]:
        """Return selected wizard values as a plain dict."""
        return {
            "theme_mode": self._theme_combo.currentData(),
            "open_environment_manager": self._open_env_check.isChecked(),
            "open_saved_credentials": self._open_creds_check.isChecked(),
        }
