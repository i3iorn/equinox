from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QCheckBox
from PyQt6.QtWidgets import QComboBox
from PyQt6.QtWidgets import QDoubleSpinBox
from PyQt6.QtWidgets import QFormLayout
from PyQt6.QtWidgets import QGroupBox
from PyQt6.QtWidgets import QHBoxLayout
from PyQt6.QtWidgets import QLabel
from PyQt6.QtWidgets import QLineEdit
from PyQt6.QtWidgets import QPushButton
from PyQt6.QtWidgets import QVBoxLayout
from PyQt6.QtWidgets import QWidget


class SettingsTabMixin:
    """Mixin providing UI-building helpers for the settings tab."""

    _policy_profile: str

    if TYPE_CHECKING:

        def _on_policy_profile_changed(self, profile: str) -> None: ...
        def _browse_cert(self) -> None: ...
        def _browse_cert_key(self) -> None: ...

    # -----------------------------
    # Public entry point
    # -----------------------------
    def create_settings_tab(
        self,
        *,
        default_timeout: float,
        browse_button_width: int,
        policy_options: tuple[str, str, str],
    ) -> QWidget:
        """Build settings tab: timeout, SSL, redirects, policy profile, and cert fields."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(4, 6, 4, 4)

        settings_group = self._build_settings_group(default_timeout, policy_options)
        cert_group = self._build_certificate_group(browse_button_width)

        layout.addWidget(settings_group)
        layout.addWidget(cert_group)
        layout.addStretch()

        return widget

    # -----------------------------
    # Settings group
    # -----------------------------
    def _build_settings_group(
        self,
        default_timeout: float,
        policy_options: tuple[str, str, str],
    ) -> QGroupBox:
        strict, balanced, permissive = policy_options

        group = QGroupBox("Request Settings")
        form = QFormLayout(group)
        form.setContentsMargins(8, 8, 8, 8)

        self._add_timeout_field(form, default_timeout)
        self._add_ssl_checkbox(form)
        self._add_redirect_checkbox(form)
        self._add_policy_profile(form, strict, balanced, permissive)

        return group

    def _add_timeout_field(self, form: QFormLayout, default_timeout: float) -> None:
        spin = QDoubleSpinBox()
        spin.setRange(1.0, 300.0)
        spin.setValue(default_timeout)
        spin.setSuffix(" s")
        spin.setDecimals(1)
        spin.setToolTip("Request timeout in seconds (1-300)")

        self.timeout_spin = spin
        form.addRow("Timeout:", spin)

    def _add_ssl_checkbox(self, form: QFormLayout) -> None:
        cb = QCheckBox("Verify SSL certificates")
        cb.setChecked(True)

        self.verify_ssl_check = cb
        form.addRow("", cb)

    def _add_redirect_checkbox(self, form: QFormLayout) -> None:
        cb = QCheckBox("Follow redirects")
        cb.setChecked(True)

        self.follow_redirects_check = cb
        form.addRow("", cb)

    def _add_policy_profile(
        self,
        form: QFormLayout,
        strict: str,
        balanced: str,
        permissive: str,
    ) -> None:
        combo = QComboBox()
        combo.addItems([strict, balanced, permissive])

        index = combo.findText(self._policy_profile)
        if index >= 0:
            combo.setCurrentIndex(index)

        combo.currentTextChanged.connect(self._on_policy_profile_changed)

        self.policy_profile_combo = combo
        form.addRow("Policy profile:", combo)

        hint = QLabel("")
        hint.setObjectName("mutedLabel")
        hint.setWordWrap(True)

        self._policy_hint = hint
        form.addRow("", hint)

        # Initialize hint immediately
        self._on_policy_profile_changed(combo.currentText())

    # -----------------------------
    # Certificate group
    # -----------------------------
    def _build_certificate_group(self, browse_button_width: int) -> QGroupBox:
        group = QGroupBox("Client Certificate (optional)")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(4, 6, 4, 4)

        layout.addLayout(self._build_cert_row(browse_button_width))
        layout.addLayout(self._build_key_row(browse_button_width))

        return group

    def _build_cert_row(self, browse_button_width: int) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(QLabel("Cert file:"))

        self.cert_path_input = QLineEdit()
        self.cert_path_input.setPlaceholderText("Path to .pem / .crt file")

        browse = QPushButton("Browse...")
        browse.setMinimumWidth(browse_button_width)
        browse.clicked.connect(self._browse_cert)

        row.addWidget(self.cert_path_input, 1)
        row.addWidget(browse)

        return row

    def _build_key_row(self, browse_button_width: int) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(QLabel("Key file:"))

        self.cert_key_input = QLineEdit()
        self.cert_key_input.setPlaceholderText("Path to private key file (leave blank if combined)")

        browse = QPushButton("Browse...")
        browse.setMinimumWidth(browse_button_width)
        browse.clicked.connect(self._browse_cert_key)

        row.addWidget(self.cert_key_input, 1)
        row.addWidget(browse)

        return row
