"""Shared GUI helpers to keep panel code DRY and consistent."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from typing import Protocol, cast

from PyQt6.QtCore import QSettings, Qt, QTimer
from PyQt6.QtWidgets import QLabel, QMessageBox, QSplitter, QTabWidget, QVBoxLayout, QWidget

_GUI_SETTINGS_ORG = "Equinox"
_GUI_SETTINGS_APP = "Equinox"

#: How often a sidebar panel re-reads its data when auto-refresh is on.
AUTO_REFRESH_INTERVAL_MS = 30_000

__all__ = [
    "AUTO_REFRESH_INTERVAL_MS",
    "AutoRefreshMixin",
    "QWidgetHostMixin",
    "canonical_tab_label",
    "confirm_yes_no",
    "configure_splitter_persistence",
    "configure_tab_bar_elision",
    "configure_tab_persistence",
    "create_muted_label",
    "create_panel_layout",
    "get_gui_settings",
    "resolve_proxy_url",
]


class QWidgetHostMixin:
    """Supplies ``_as_qwidget()`` to mixins whose host is a QWidget.

    Qt APIs that take a parent want a real QWidget, but a mixin is not one as
    far as the type checker is concerned. Mix this in rather than restating
    the cast: it had grown twelve copies in four spellings, including one
    that reached for ``# type: ignore`` and one that cast twice.
    """

    def _as_qwidget(self) -> QWidget:
        """Return this mixin host as a QWidget for Qt parent arguments."""
        return cast(QWidget, self)


class _RefreshablePanel(Protocol):
    """The host contract AutoRefreshMixin relies on (a QWidget with refresh)."""

    # Qt naming, matched so the cast lines up with the real QWidget method.
    def isVisible(self) -> bool: ...

    def refresh(self) -> None: ...


class AutoRefreshMixin:
    """Periodically re-read a panel's data while it is visible.

    Mix in *before* QWidget on a panel that defines ``refresh()``, set
    ``auto_refresh_enabled`` before calling ``_setup_auto_refresh()``, and
    wire a checkbox's ``stateChanged`` to ``_toggle_auto_refresh``.

    Deliberately declares no ``isVisible``/``refresh`` stubs: this sits ahead
    of QWidget in the MRO, so a stub here would shadow the real Qt method.
    The host contract is expressed as a Protocol and reached through a cast.
    """

    auto_refresh_enabled: bool
    refresh_timer: QTimer

    def _setup_auto_refresh(self) -> None:
        """Start the periodic refresh timer."""
        self.refresh_timer = QTimer(cast(QWidget, self))
        self.refresh_timer.timeout.connect(self._refresh_if_visible)
        self.refresh_timer.start(AUTO_REFRESH_INTERVAL_MS)

    def _refresh_if_visible(self) -> None:
        """Refresh only when on screen, so hidden tabs cost nothing."""
        host = cast(_RefreshablePanel, self)
        if host.isVisible():
            host.refresh()

    def _toggle_auto_refresh(self, state: int) -> None:
        """Start or stop the timer from a checkbox's ``stateChanged``.

        Compares against ``Checked`` rather than testing truthiness, because
        ``PartiallyChecked`` is 1 and would otherwise read as enabled.
        """
        self.auto_refresh_enabled = Qt.CheckState(state) == Qt.CheckState.Checked
        if self.auto_refresh_enabled:
            self.refresh_timer.start(AUTO_REFRESH_INTERVAL_MS)
            return
        self.refresh_timer.stop()


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


def configure_tab_bar_elision(
    tab_widget: QTabWidget,
    *,
    tooltip_for: Callable[[int], str] | None = None,
) -> None:
    """Keep every tab visible and clickable instead of behind scroll arrows.

    Qt's default for a tab bar too narrow for its labels is to hide the
    overflow behind small ``‹ ›`` arrows, which silently makes some tabs
    unreachable. Eliding instead keeps every tab on screen — shortened, but
    with the full name in a tooltip.

    ``tooltip_for`` maps a tab index to its tooltip text, for callers that
    need something other than the literal tab text (e.g. a canonical label
    plus a keyboard shortcut, so a badge suffix never leaks into the tooltip).
    """
    bar = tab_widget.tabBar()
    if bar is None:  # pragma: no cover - defensive, Qt always builds one
        return
    bar.setUsesScrollButtons(False)
    bar.setExpanding(False)
    bar.setElideMode(Qt.TextElideMode.ElideRight)
    resolve = tooltip_for or tab_widget.tabText
    for index in range(tab_widget.count()):
        tab_widget.setTabToolTip(index, resolve(index))


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
