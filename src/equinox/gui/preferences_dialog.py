"""Preferences dialog — user-configurable appearance settings."""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QSlider,
    QDialogButtonBox, QGroupBox, QFormLayout, QSpinBox,
    QComboBox, QLineEdit,
)
from PyQt6.QtCore import Qt, QSettings

from equinox.gui.theme import (
    Colors, get_font_size, set_font_size, MIN_FONT_SIZE, MAX_FONT_SIZE,
    get_theme_mode, set_theme_mode, THEME_MODES, THEME_LABELS,
)


class PreferencesDialog(QDialog):
    """Preferences dialog — theme mode and font size."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._settings = QSettings("Equinox", "Equinox")
        self.setWindowTitle("Preferences")
        self.setMinimumWidth(440)
        self._original_size = get_font_size()
        self._original_theme = get_theme_mode()
        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(16)

        # ── Appearance group ──────────────────────────────────────────
        group = QGroupBox("Appearance")
        form  = QFormLayout(group)
        form.setSpacing(12)

        # Theme mode — combo box
        self._theme_combo = QComboBox()
        for mode in THEME_MODES:
            self._theme_combo.addItem(THEME_LABELS[mode], userData=mode)
        current_idx = list(THEME_MODES).index(self._original_theme)
        self._theme_combo.setCurrentIndex(current_idx)
        self._theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        form.addRow("Theme:", self._theme_combo)

        # Font size — slider + spin box
        size_row = QHBoxLayout()
        size_row.setSpacing(8)

        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(MIN_FONT_SIZE, MAX_FONT_SIZE)
        self._slider.setValue(self._original_size)
        self._slider.setTickInterval(1)
        self._slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._slider.setMinimumWidth(180)

        self._spin = QSpinBox()
        self._spin.setRange(MIN_FONT_SIZE, MAX_FONT_SIZE)
        self._spin.setSuffix(" pt")
        self._spin.setValue(self._original_size)

        self._slider.valueChanged.connect(self._spin.setValue)
        self._spin.valueChanged.connect(self._slider.setValue)
        self._spin.valueChanged.connect(self._on_size_changed)

        size_row.addWidget(self._slider, 1)
        size_row.addWidget(self._spin)

        form.addRow("Font size:", size_row)

        # Preview
        self._preview = QLabel(
            "The quick brown fox jumps over the lazy dog — 0123456789\n"
            "GET  https://api.example.com/v1/users"
        )
        self._preview.setWordWrap(True)
        self._update_preview(self._original_size)
        form.addRow("Preview:", self._preview)

        layout.addWidget(group)

        # ── Network group ─────────────────────────────────────────────
        net_group = QGroupBox("Network")
        net_form  = QFormLayout(net_group)
        net_form.setSpacing(10)

        self._proxy_host = QLineEdit()
        self._proxy_host.setPlaceholderText("hostname or IP  (leave blank to disable)")
        self._proxy_host.setText(self._settings.value("proxy/host", ""))
        net_form.addRow("Proxy host:", self._proxy_host)

        self._proxy_port = QSpinBox()
        self._proxy_port.setRange(0, 65535)
        self._proxy_port.setSpecialValueText("(disabled)")
        self._proxy_port.setValue(int(self._settings.value("proxy/port", 0) or 0))
        net_form.addRow("Proxy port:", self._proxy_port)

        layout.addWidget(net_group)

        # ── Buttons ───────────────────────────────────────────────────
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.RestoreDefaults,
        )
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self._cancel)
        btns.button(QDialogButtonBox.StandardButton.RestoreDefaults).clicked.connect(
            self._restore_defaults
        )
        layout.addWidget(btns)

    # ── Callbacks ─────────────────────────────────────────────────────

    def _on_theme_changed(self, _index: int) -> None:
        mode = self._theme_combo.currentData()
        set_theme_mode(mode)
        self._update_preview(self._spin.value())

    def _on_size_changed(self, size: int) -> None:
        set_font_size(size)
        self._update_preview(size)

    def _update_preview(self, size: int) -> None:
        self._preview.setStyleSheet(
            f"padding: 8px; border: 1px solid {Colors.BORDER}; border-radius: 4px; "
            f"background: {Colors.BG_ALT}; color: {Colors.FG}; font-size: {size}pt;"
        )

    def _restore_defaults(self) -> None:
        from equinox.gui.theme import DEFAULT_FONT_SIZE, THEME_SYSTEM
        self._spin.setValue(DEFAULT_FONT_SIZE)
        idx = list(THEME_MODES).index(THEME_SYSTEM)
        self._theme_combo.setCurrentIndex(idx)

    def _accept(self) -> None:
        self._settings.setValue("proxy/host", self._proxy_host.text().strip())
        self._settings.setValue("proxy/port", self._proxy_port.value())
        self.accept()

    def _cancel(self) -> None:
        # Revert to original settings
        set_font_size(self._original_size)
        set_theme_mode(self._original_theme)
        self.reject()

