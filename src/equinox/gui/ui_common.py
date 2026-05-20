"""Shared GUI helpers to keep panel code DRY and consistent."""

from __future__ import annotations

import logging
from collections.abc import Iterable

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QLabel, QMessageBox, QSplitter, QTabWidget, QVBoxLayout, QWidget

_GUI_SETTINGS_ORG = "Equinox"
_GUI_SETTINGS_APP = "Equinox"

__all__ = [
    "canonical_tab_label",
    "confirm_yes_no",
    "configure_splitter_persistence",
    "configure_tab_persistence",
    "create_muted_label",
    "create_panel_layout",
    "get_gui_settings",
    "resolve_proxy_url",
]


def get_gui_settings() -> QSettings:
    """Return the app-wide QSettings handle used by GUI components."""
    return QSettings(_GUI_SETTINGS_ORG, _GUI_SETTINGS_APP)


def resolve_proxy_url(
    *,
    settings: QSettings | None = None,
    logger: logging.Logger | None = None,
) -> str | None:
    """Resolve proxy URL from settings with safe parsing.

    Returns ``None`` if host/port are missing or invalid.
    """
    source = settings or get_gui_settings()
    host = str(source.value("proxy/host") or "").strip()
    raw_port = source.value("proxy/port")
    try:
        port = int(raw_port or 0)
    except (TypeError, ValueError):
        if logger is not None:
            logger.warning("gui.proxy_port_invalid op=resolve_proxy_url raw_port=%r", raw_port)
        return None
    if not host or port <= 0:
        return None
    return f"http://{host}:{port}"


def create_panel_layout(parent: QWidget, *, margin: int = 4, spacing: int = 4) -> QVBoxLayout:
    """Create a standard panel layout with shared spacing defaults."""
    layout = QVBoxLayout(parent)
    layout.setContentsMargins(margin, margin, margin, margin)
    layout.setSpacing(spacing)
    return layout


def create_muted_label(text: str = "") -> QLabel:
    """Create a label that uses the app's muted text style."""
    label = QLabel(text)
    label.setObjectName("mutedLabel")
    return label


def confirm_yes_no(parent: QWidget, title: str, question: str) -> bool:
    """Show a standard yes/no confirmation dialog."""
    reply = QMessageBox.question(
        parent,
        title,
        question,
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
    )
    return reply == QMessageBox.StandardButton.Yes


def configure_splitter_persistence(
    splitter: QSplitter,
    *,
    settings_key: str,
    default_sizes: Iterable[int],
    settings: QSettings | None = None,
) -> None:
    """Restore splitter sizes and auto-persist future changes to QSettings."""
    source = settings or get_gui_settings()
    saved = source.value(settings_key)
    sizes = list(default_sizes)
    if saved:
        try:
            sizes = [int(value) for value in saved]
        except Exception:
            pass
    splitter.setSizes(sizes)
    splitter.splitterMoved.connect(lambda: source.setValue(settings_key, splitter.sizes()))


def canonical_tab_label(label: str) -> str:
    """Normalize a tab label so persistence ignores badges and marker glyphs."""
    text = str(label or "").strip()
    if text.endswith("●"):
        text = text[:-1].rstrip()
    badge_start = text.rfind(" (")
    if badge_start != -1 and text.endswith(")"):
        text = text[:badge_start].rstrip()
    return text


def configure_tab_persistence(
    tab_widget: QTabWidget,
    *,
    settings_key: str,
    default_tab: str | None = None,
    settings: QSettings | None = None,
) -> None:
    """Restore the selected tab by logical label and persist future changes."""
    source = settings or get_gui_settings()
    saved_label = canonical_tab_label(str(source.value(settings_key) or ""))
    target_label = saved_label or canonical_tab_label(default_tab or "")

    if target_label:
        for idx in range(tab_widget.count()):
            if canonical_tab_label(tab_widget.tabText(idx)) == target_label:
                tab_widget.setCurrentIndex(idx)
                break

    def _persist_tab(index: int) -> None:
        if 0 <= index < tab_widget.count():
            source.setValue(settings_key, canonical_tab_label(tab_widget.tabText(index)))

    tab_widget.currentChanged.connect(_persist_tab)
    _persist_tab(tab_widget.currentIndex())
