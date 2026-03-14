"""Preferences dialog — user-configurable appearance settings."""

import json as _json

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QSlider,
    QDialogButtonBox, QGroupBox, QFormLayout, QSpinBox,
    QComboBox, QLineEdit, QCheckBox, QScrollArea, QWidget,
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

        # ── Intelligence group ────────────────────────────────────────
        intel_group = QGroupBox("Response Intelligence")
        intel_layout = QVBoxLayout(intel_group)

        intel_desc = QLabel(
            "Select which analyzers run automatically after each response."
        )
        intel_desc.setWordWrap(True)
        intel_desc.setStyleSheet(f"color: {Colors.FG_MUTED};")
        intel_layout.addWidget(intel_desc)

        # Load disabled set from settings
        disabled_raw = self._settings.value("intelligence/disabled_analyzers", "[]")
        try:
            self._disabled_set = set(_json.loads(disabled_raw)) if disabled_raw else set()
        except Exception:
            self._disabled_set = set()

        # Scroll area for checkboxes
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(200)
        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout(scroll_widget)
        scroll_layout.setContentsMargins(4, 4, 4, 4)
        scroll_layout.setSpacing(2)

        self._analyzer_checks: list = []
        try:
            from equinox.core.response_intelligence.engine import AnalysisEngine
            all_info = AnalysisEngine().get_all_analyzer_info()
            current_cat = ""
            for info in all_info:
                cat = info["category"]
                if cat != current_cat:
                    current_cat = cat
                    cat_label = QLabel(f"── {cat} ──")
                    cat_label.setStyleSheet(
                        f"font-weight: bold; color: {Colors.FG_MUTED}; "
                        f"padding-top: 4px; font-size: 11px;"
                    )
                    scroll_layout.addWidget(cat_label)
                cb = QCheckBox(info["name"])
                cb.setChecked(info["id"] not in self._disabled_set)
                cb.setProperty("analyzer_id", info["id"])
                scroll_layout.addWidget(cb)
                self._analyzer_checks.append(cb)
        except Exception:
            scroll_layout.addWidget(QLabel("(Could not load analyzers)"))

        scroll_layout.addStretch()
        scroll.setWidget(scroll_widget)
        intel_layout.addWidget(scroll)

        layout.addWidget(intel_group)

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

        # Save disabled analyzers
        disabled = []
        for cb in self._analyzer_checks:
            if not cb.isChecked():
                disabled.append(cb.property("analyzer_id"))
        self._settings.setValue(
            "intelligence/disabled_analyzers", _json.dumps(disabled)
        )

        self.accept()

    def _cancel(self) -> None:
        # Revert to original settings
        set_font_size(self._original_size)
        set_theme_mode(self._original_theme)
        self.reject()

