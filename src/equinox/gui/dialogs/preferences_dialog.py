"""Preferences dialog — user-configurable appearance settings."""

from __future__ import annotations

import json as _json
from typing import Any

from equinox.gui.error_presenter import ErrorPresenter
from equinox.gui.theme import DEFAULT_FONT_SIZE
from equinox.gui.theme import get_font_size
from equinox.gui.theme import get_theme_mode
from equinox.gui.theme import MAX_FONT_SIZE
from equinox.gui.theme import MIN_FONT_SIZE
from equinox.gui.theme import set_font_size
from equinox.gui.theme import set_theme_mode
from equinox.gui.theme import THEME_LABELS
from equinox.gui.theme import THEME_MODES
from equinox.gui.theme import THEME_SYSTEM
from PyQt6.QtCore import QSettings
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QCheckBox
from PyQt6.QtWidgets import QComboBox
from PyQt6.QtWidgets import QDialog
from PyQt6.QtWidgets import QDialogButtonBox
from PyQt6.QtWidgets import QFormLayout
from PyQt6.QtWidgets import QGroupBox
from PyQt6.QtWidgets import QHBoxLayout
from PyQt6.QtWidgets import QLabel
from PyQt6.QtWidgets import QLineEdit
from PyQt6.QtWidgets import QScrollArea
from PyQt6.QtWidgets import QSlider
from PyQt6.QtWidgets import QSpinBox
from PyQt6.QtWidgets import QVBoxLayout
from PyQt6.QtWidgets import QWidget
from equinox.gui.ui_common import create_panel_layout

# ── Module-level constants ────────────────────────────────────────────────────
_SETTINGS_ORG = "Equinox"
_SETTINGS_APP = "Equinox"

_PREVIEW_TEXT = (
    "The quick brown fox jumps over the lazy dog — 0123456789\n"
    "GET  https://api.example.com/v1/users"
)

_ANALYZER_SCROLL_MAX_H = 200
_RECOMMENDER_ANALYZER_ID = "recommender"


class PreferencesDialog(QDialog):
    """Preferences dialog — theme mode, font size, network and intelligence settings."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = QSettings(_SETTINGS_ORG, _SETTINGS_APP)
        self.setWindowTitle("Preferences")
        self.setMinimumWidth(440)
        self._original_size = get_font_size()
        self._original_theme = get_theme_mode()
        self._analyzer_checks: list[QCheckBox] = []
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
        restore_btn = btns.button(QDialogButtonBox.StandardButton.RestoreDefaults)
        if restore_btn is not None:
            restore_btn.clicked.connect(self._restore_defaults)
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
        self._slider.setTickPosition(QSlider.TickPosition.TicksBothSides)
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

        # Live preview — the label's actual size/theme comes from the
        # app-wide stylesheet, which set_font_size()/set_theme_mode() below
        # re-apply immediately (QApplication.setFont + stylesheet), so this
        # widget needs no dedicated per-value refresh hook.
        self._preview = QLabel(_PREVIEW_TEXT)
        self._preview.setWordWrap(True)
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
        """Build the Response Intelligence settings group."""
        group = QGroupBox("Response Intelligence")
        layout = QVBoxLayout(group)

        layout.addWidget(self._build_intel_description())
        self._disabled_set = self._load_disabled_analyzer_set()

        scroll_area = self._build_analyzer_scroll_area()
        layout.addWidget(scroll_area)

        return group

    def _build_intel_description(self) -> QLabel:
        label = QLabel("Select which analyzers run automatically after each response.")
        label.setWordWrap(True)
        return label

    def _load_disabled_analyzer_set(self) -> set[str]:
        raw = self._settings.value("intelligence/disabled_analyzers", "[]")
        try:
            return set(_json.loads(raw)) if raw else set()
        except (_json.JSONDecodeError, TypeError, ValueError):
            return set()

    def _build_analyzer_scroll_area(self) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(_ANALYZER_SCROLL_MAX_H)

        widget = QWidget()
        layout = create_panel_layout(widget, spacing=2)

        widget.setUpdatesEnabled(False)
        try:
            self._populate_analyzer_list(layout)
        except Exception:
            layout.addWidget(QLabel("(Could not load analyzers)"))
        finally:
            widget.setUpdatesEnabled(True)

        layout.addStretch()
        scroll.setWidget(widget)
        return scroll

    def _populate_analyzer_list(self, layout: QVBoxLayout) -> None:
        from equinox.core.response_intelligence import AnalysisEngine

        analyzer_ids: set[str] = set()
        current_category = ""

        for info in AnalysisEngine().get_all_analyzer_info():
            current_category = self._maybe_add_category_header(
                layout,
                current_category,
                info["category"],
            )
            self._add_analyzer_checkbox(layout, info)
            analyzer_ids.add(str(info.get("id") or ""))

        self._maybe_add_recommender(layout, analyzer_ids)

    def _maybe_add_category_header(
        self,
        layout: QVBoxLayout,
        current_category: str,
        new_category: str,
    ) -> str:
        if new_category != current_category:
            header = QLabel(f"── {new_category} ──")
            layout.addWidget(header)
            return new_category
        return current_category

    def _add_analyzer_checkbox(self, layout: QVBoxLayout, info: dict[str, Any]) -> None:
        cb = QCheckBox(info["name"])
        cb.setChecked(info["id"] not in self._disabled_set)
        cb.setProperty("analyzer_id", info["id"])
        layout.addWidget(cb)
        self._analyzer_checks.append(cb)

    def _maybe_add_recommender(self, layout: QVBoxLayout, analyzer_ids: set[str]) -> None:
        if _RECOMMENDER_ANALYZER_ID in analyzer_ids:
            return

        header = QLabel("── Developer Hints ──")
        layout.addWidget(header)

        cb = QCheckBox("Request Recommender")
        cb.setChecked(_RECOMMENDER_ANALYZER_ID not in self._disabled_set)
        cb.setProperty("analyzer_id", _RECOMMENDER_ANALYZER_ID)
        layout.addWidget(cb)
        self._analyzer_checks.append(cb)

    # ── Callbacks ─────────────────────────────────────────────────────

    def _on_theme_changed(self, _index: int) -> None:
        set_theme_mode(self._theme_combo.currentData())

    def _on_size_changed(self, size: int) -> None:
        set_font_size(size)

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
            ErrorPresenter.warning(
                self,
                "To enable a proxy, enter both a host and a port.\n\n"
                "Leave both fields empty to disable the proxy.",
                title="Incomplete Proxy Configuration",
            )
            return

        self._settings.setValue("proxy/host", proxy_host)
        self._settings.setValue("proxy/port", proxy_port)

        disabled = [
            cb.property("analyzer_id") for cb in self._analyzer_checks if not cb.isChecked()
        ]
        self._settings.setValue("intelligence/disabled_analyzers", _json.dumps(disabled))
        self.accept()

    def _cancel(self) -> None:
        self.reject()

    def reject(self) -> None:
        """Revert the live-previewed theme/font size before closing.

        The theme combo and font-size slider apply immediately as the user
        interacts with them (see ``_on_theme_changed``/``_on_size_changed``),
        so cancelling must restore the original values. This must live in
        ``reject()`` rather than only in ``_cancel()``, since Escape and the
        native window-close button call ``QDialog.reject()`` directly and
        never go through the Cancel button's ``rejected`` signal.
        """
        set_font_size(self._original_size)
        set_theme_mode(self._original_theme)
        super().reject()
