"""Preferences dialog — user-configurable appearance settings."""

from __future__ import annotations

import json as _json
from typing import List

from PyQt6.QtCore import Qt, QSettings
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QScrollArea, QSlider, QSpinBox, QVBoxLayout, QWidget,
)

from equinox.gui.theme import (
    Colors,
    DEFAULT_FONT_SIZE, MIN_FONT_SIZE, MAX_FONT_SIZE,
    THEME_LABELS, THEME_MODES, THEME_SYSTEM,
    get_font_size, get_theme_mode, set_font_size, set_theme_mode,
)

# ── Module-level constants ────────────────────────────────────────────────────
_SETTINGS_ORG = "Equinox"
_SETTINGS_APP = "Equinox"

_PREVIEW_TEXT = (
    "The quick brown fox jumps over the lazy dog — 0123456789\n"
    "GET  https://api.example.com/v1/users"
)

_ANALYZER_SCROLL_MAX_H = 200


class PreferencesDialog(QDialog):
    """Preferences dialog — theme mode, font size, network and intelligence settings."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._settings = QSettings(_SETTINGS_ORG, _SETTINGS_APP)
        self.setWindowTitle("Preferences")
        self.setMinimumWidth(440)
        self._original_size = get_font_size()
        self._original_theme = get_theme_mode()
        self._analyzer_checks: List[QCheckBox] = []
        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(16)

        layout.addWidget(self._build_appearance_group())
        layout.addWidget(self._build_network_group())
        layout.addWidget(self._build_intelligence_group())

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

    # ── Group builders ────────────────────────────────────────────────

    def _build_appearance_group(self) -> QGroupBox:
        """Theme-mode selector, font-size slider/spin, and live preview."""
        group = QGroupBox("Appearance")
        form = QFormLayout(group)
        form.setSpacing(12)

        # Theme mode
        self._theme_combo = QComboBox()
        for mode in THEME_MODES:
            self._theme_combo.addItem(THEME_LABELS[mode], userData=mode)
        self._theme_combo.setCurrentIndex(list(THEME_MODES).index(self._original_theme))
        self._theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        form.addRow("Theme:", self._theme_combo)

        # Font size — slider + spin box (kept in sync via valueChanged)
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

        # Live preview
        self._preview = QLabel(_PREVIEW_TEXT)
        self._preview.setWordWrap(True)
        self._update_preview(self._original_size)
        form.addRow("Preview:", self._preview)

        return group

    def _build_network_group(self) -> QGroupBox:
        """Proxy host and port settings."""
        net_group = QGroupBox("Network")
        net_form = QFormLayout(net_group)
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

        return net_group

    def _build_intelligence_group(self) -> QGroupBox:
        """Per-analyzer enable/disable checkboxes with category headings."""
        intel_group = QGroupBox("Response Intelligence")
        intel_layout = QVBoxLayout(intel_group)

        desc = QLabel("Select which analyzers run automatically after each response.")
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color: {Colors.FG_MUTED};")
        intel_layout.addWidget(desc)

        # Load the disabled-analyzer set from persistent settings
        disabled_raw = self._settings.value("intelligence/disabled_analyzers", "[]")
        try:
            self._disabled_set: set = (
                set(_json.loads(disabled_raw)) if disabled_raw else set()
            )
        except (_json.JSONDecodeError, TypeError, ValueError):
            self._disabled_set = set()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(_ANALYZER_SCROLL_MAX_H)
        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout(scroll_widget)
        scroll_layout.setContentsMargins(4, 4, 4, 4)
        scroll_layout.setSpacing(2)

        scroll_widget.setUpdatesEnabled(False)
        try:
            from equinox.core.response_intelligence.engine import AnalysisEngine
            current_cat = ""
            for info in AnalysisEngine().get_all_analyzer_info():
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
        finally:
            scroll_widget.setUpdatesEnabled(True)

        scroll_layout.addStretch()
        scroll.setWidget(scroll_widget)
        intel_layout.addWidget(scroll)

        return intel_group

    # ── Callbacks ─────────────────────────────────────────────────────

    def _on_theme_changed(self, _index: int) -> None:
        set_theme_mode(self._theme_combo.currentData())
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
        self._spin.setValue(DEFAULT_FONT_SIZE)
        self._theme_combo.setCurrentIndex(list(THEME_MODES).index(THEME_SYSTEM))
        self._proxy_host.clear()
        self._proxy_port.setValue(0)

    def _accept(self) -> None:
        proxy_host = self._proxy_host.text().strip()
        proxy_port = self._proxy_port.value()

        # Require both host and port, or neither — flag any half-configured state
        if bool(proxy_host) != bool(proxy_port):
            QMessageBox.warning(
                self,
                "Incomplete Proxy Configuration",
                "To enable a proxy, enter both a host and a port.\n\n"
                "Leave both fields empty to disable the proxy.",
            )
            return

        self._settings.setValue("proxy/host", proxy_host)
        self._settings.setValue("proxy/port", proxy_port)

        disabled = [
            cb.property("analyzer_id")
            for cb in self._analyzer_checks
            if not cb.isChecked()
        ]
        self._settings.setValue("intelligence/disabled_analyzers", _json.dumps(disabled))
        self.accept()

    def _cancel(self) -> None:
        set_font_size(self._original_size)
        set_theme_mode(self._original_theme)
        self.reject()

