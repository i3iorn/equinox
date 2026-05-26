"""Frameless window, resize, cursor, and zoom/theme mixin for MainWindow."""

# mypy: disable-error-code=attr-defined

from __future__ import annotations

import logging
from typing import Any, cast

from PyQt6.QtCore import QEvent, QObject, QPoint, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QMainWindow, QWidget

from equinox.gui.theme import (
    DEFAULT_FONT_SIZE,
    get_font_size,
    get_theme_mode,
    set_font_size,
    set_theme_mode,
)

logger = logging.getLogger(__name__)

# Window edge grab-zone for frameless resize.
_RESIZE_BORDER_PX = 6
_MAX_RESIZE_BORDER_PX = 14


class _FramelessMixin:
    """Frameless-window resize/drag behavior and zoom/theme controls."""

    _theme_actions: dict[str, Any]
    _win_max_btn: Any
    _drag_menu_active: bool
    _resize_active: bool
    _drag_handles: set[QObject]
    _drag_menu_offset: QPoint

    def _main_window(self) -> QMainWindow:
        return cast(QMainWindow, self)

    # ── Zoom ──────────────────────────────────────────────────────────────────

    def _adjust_font_size(self, delta: int) -> None:
        """Clamp and apply a font-size change of *delta* points."""
        set_font_size(get_font_size() + delta)

    def _zoom_in(self) -> None:
        self._adjust_font_size(+1)

    def _zoom_out(self) -> None:
        self._adjust_font_size(-1)

    def _zoom_reset(self) -> None:
        set_font_size(DEFAULT_FONT_SIZE)

    def _set_theme(self, mode: str) -> None:
        set_theme_mode(mode)
        self._sync_theme_checks()

    def _sync_theme_checks(self) -> None:
        """Keep the theme radio-check marks in sync with the current mode."""
        current = get_theme_mode()
        for mode, action in self._theme_actions.items():
            action.setChecked(mode == current)

    # ── Window control buttons ─────────────────────────────────────────────

    def _toggle_max_restore(self) -> None:
        """Toggle between maximized and normal states."""
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()
        self._sync_window_controls()

    def _sync_window_controls(self) -> None:
        """Update maximize button icon/tooltip to reflect current window state."""
        if not hasattr(self, "_win_max_btn"):
            return
        if self.isMaximized():
            self._win_max_btn.setText("❐")
            self._win_max_btn.setToolTip("Restore")
        else:
            self._win_max_btn.setText("□")
            self._win_max_btn.setToolTip("Maximize")

    def changeEvent(self, event: QEvent) -> None:
        if event.type() == QEvent.Type.WindowStateChange:
            self._sync_window_controls()
            if not self._can_resize_frameless():
                self.setCursor(Qt.CursorShape.ArrowCursor)
        QMainWindow.changeEvent(self._main_window(), event)

    # ── Frameless resize helpers ───────────────────────────────────────────

    def _resize_edges_for_pos(self, pos: QPoint) -> Qt.Edge:
        """Return the window-edge flags under *pos* for frameless resizing."""
        x = pos.x()
        y = pos.y()
        w = self.width()
        h = self.height()
        border_px = self._effective_resize_border_px()

        edges = Qt.Edge(0)
        if x <= border_px:
            edges |= Qt.Edge.LeftEdge
        elif x >= w - border_px:
            edges |= Qt.Edge.RightEdge
        if y <= border_px:
            edges |= Qt.Edge.TopEdge
        elif y >= h - border_px:
            edges |= Qt.Edge.BottomEdge
        return edges

    def _effective_resize_border_px(self) -> int:
        """Scale resize hit target for high-DPI displays while keeping sane bounds."""
        ratio = 1.0
        try:
            ratio = float(self.devicePixelRatioF())
        except Exception:
            ratio = 1.0
        scaled = int(round(_RESIZE_BORDER_PX * max(1.0, ratio)))
        return max(_RESIZE_BORDER_PX, min(_MAX_RESIZE_BORDER_PX, scaled))

    def _can_resize_frameless(self) -> bool:
        """Frameless resize is enabled only in normal windowed mode."""
        return not self.isMaximized() and not self.isFullScreen()

    def _cursor_for_edges(self, edges: Qt.Edge) -> Qt.CursorShape:
        """Map edge flags to the expected resize cursor shape."""
        has_left = bool(edges & Qt.Edge.LeftEdge)
        has_right = bool(edges & Qt.Edge.RightEdge)
        has_top = bool(edges & Qt.Edge.TopEdge)
        has_bottom = bool(edges & Qt.Edge.BottomEdge)

        if (has_left and has_top) or (has_right and has_bottom):
            return Qt.CursorShape.SizeFDiagCursor
        if (has_right and has_top) or (has_left and has_bottom):
            return Qt.CursorShape.SizeBDiagCursor
        if has_left or has_right:
            return Qt.CursorShape.SizeHorCursor
        if has_top or has_bottom:
            return Qt.CursorShape.SizeVerCursor
        return Qt.CursorShape.ArrowCursor

    def _update_resize_cursor(self, pos: QPoint) -> None:
        """Show resize cursors near window borders when resize is available."""
        if not self._can_resize_frameless() or self._drag_menu_active:
            self.setCursor(Qt.CursorShape.ArrowCursor)
            return
        edges = self._resize_edges_for_pos(pos)
        self.setCursor(self._cursor_for_edges(edges))

    def _update_resize_cursor_from_global(self, global_pos: QPoint) -> None:
        """Update resize cursor from a global point emitted by child widgets."""
        self._update_resize_cursor(self.mapFromGlobal(global_pos))

    def _handle_frameless_resize_event(self, watched: QObject, event: QEvent) -> bool:
        """Handle resize/cursor behavior for this window from child widget events."""
        if not isinstance(event, QMouseEvent):
            return False
        if not isinstance(watched, QWidget):
            return False
        if watched.window() is not self:  # type: ignore[comparison-overlap]
            return False

        if event.type() == QEvent.Type.MouseMove:
            self._update_resize_cursor_from_global(event.globalPosition().toPoint())
            return False

        if (
            event.type() == QEvent.Type.MouseButtonPress
            and event.button() == Qt.MouseButton.LeftButton
            and self._can_resize_frameless()
            and not self._drag_menu_active
        ):
            local_pos = self.mapFromGlobal(event.globalPosition().toPoint())
            edges = self._resize_edges_for_pos(local_pos)
            if edges:
                handle = self.windowHandle()
                if handle is not None and handle.startSystemResize(edges):
                    self._resize_active = True
                    event.accept()
                    return True

        if (
            event.type() == QEvent.Type.MouseButtonRelease
            and event.button() == Qt.MouseButton.LeftButton
        ):
            self._resize_active = False
            self._update_resize_cursor_from_global(event.globalPosition().toPoint())

        return False

    # ── Qt event overrides ────────────────────────────────────────────────────

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Enable dragging the frameless window from empty menu-bar/title area."""
        if self._handle_frameless_resize_event(watched, event):
            return True

        if (
            watched in self._drag_handles
            and event.type() == QEvent.Type.MouseButtonPress
            and event.button() == Qt.MouseButton.LeftButton
        ):
            menu_bar = self.menuBar()
            if watched is menu_bar:
                action = menu_bar.actionAt(event.pos())
                if action is not None:
                    self._drag_menu_active = False
                    return bool(QMainWindow.eventFilter(self._main_window(), watched, event))
            self._drag_menu_active = not self.isMaximized() and not self.isFullScreen()
            if self._drag_menu_active:
                self._drag_menu_offset = (
                    event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                )
            return False

        if (
            watched in self._drag_handles
            and event.type() == QEvent.Type.MouseMove
            and self._drag_menu_active
        ):
            pressed_buttons = {b.value for b in event.buttons()}
            if Qt.MouseButton.LeftButton.value in pressed_buttons:
                self.move(event.globalPosition().toPoint() - self._drag_menu_offset)
                return True
            self._drag_menu_active = False

        if (
            watched in self._drag_handles
            and event.type() == QEvent.Type.MouseButtonRelease
            and event.button() == Qt.MouseButton.LeftButton
        ):
            self._drag_menu_active = False

        return bool(QMainWindow.eventFilter(self._main_window(), watched, event))

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._can_resize_frameless()
            and not self._drag_menu_active
        ):
            edges = self._resize_edges_for_pos(event.position().toPoint())
            if edges:
                handle = self.windowHandle()
                if handle is not None and handle.startSystemResize(edges):
                    self._resize_active = True
                    event.accept()
                    return
        QMainWindow.mousePressEvent(self._main_window(), event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        self._update_resize_cursor(event.position().toPoint())
        QMainWindow.mouseMoveEvent(self._main_window(), event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._resize_active = False
        self._update_resize_cursor(event.position().toPoint())
        QMainWindow.mouseReleaseEvent(self._main_window(), event)

    def leaveEvent(self, event: QEvent) -> None:
        if not self._resize_active:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        QMainWindow.leaveEvent(self._main_window(), event)
