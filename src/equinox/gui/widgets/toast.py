"""Non-blocking toast notifications for transient GUI feedback.

Toasts appear briefly at the bottom of the main window (above the status bar)
and auto-dismiss. They are intentionally lightweight: no modal, no focus
stealing, and no user action required. Multiple toasts stack vertically.
"""

from __future__ import annotations

import logging
from typing import Any

from PyQt6.QtCore import QEasingCurve
from PyQt6.QtCore import QPropertyAnimation
from PyQt6.QtCore import QTimer
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QGraphicsOpacityEffect
from PyQt6.QtWidgets import QHBoxLayout
from PyQt6.QtWidgets import QLabel
from PyQt6.QtWidgets import QToolButton
from PyQt6.QtWidgets import QVBoxLayout
from PyQt6.QtWidgets import QWidget

logger = logging.getLogger(__name__)

# Default auto-dismiss duration (ms)
_TOAST_DURATION_MS = 4000
# Animation duration (ms)
_ANIM_DURATION_MS = 220


class ToastWidget(QWidget):
    """A single dismissible toast banner.

    Slides up from below its container, holds, then fades out and removes
    itself from its layout.
    """

    def __init__(
        self,
        message: str,
        *,
        kind: str = "info",
        duration_ms: int = _TOAST_DURATION_MS,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("toastWidget")

        # Fade-in / fade-out effect.
        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(8)

        # Kind indicator icon.
        icon_map = {
            "success": "✓",
            "warning": "⚠",
            "error": "✕",
            "info": "ℹ",
        }
        icon = QLabel(icon_map.get(kind, "ℹ"))
        icon.setFixedSize(18, 18)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setObjectName("toastIcon")
        layout.addWidget(icon)

        # Message text.
        self._message_label = QLabel(message)
        self._message_label.setWordWrap(True)
        self._message_label.setObjectName("toastMessage")
        layout.addWidget(self._message_label, 1)

        # Dismiss button.
        dismiss = QToolButton()
        dismiss.setText("✕")
        dismiss.setFixedSize(20, 20)
        dismiss.setObjectName("toastDismiss")
        dismiss.setCursor(Qt.CursorShape.PointingHandCursor)
        dismiss.clicked.connect(self.dismiss)
        layout.addWidget(dismiss)

        # Start hidden, then animate in.
        self._opacity.setOpacity(0.0)
        self.hide()

        # Auto-dismiss timer.
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)
        self._duration_ms = duration_ms

        # Apply kind-specific styling via dynamic property so the QSS can target
        # it without hardcoding colors here.
        self.setProperty("toastKind", kind)
        style = self.style()
        if style is not None:
            style.unpolish(self)
            style.polish(self)

    def showEvent(self, event: Any) -> None:
        super().showEvent(event)
        self._animate_in()
        self._timer.start(self._duration_ms)

    def _animate_in(self) -> None:
        """Fade the toast in from fully transparent."""
        self.show()
        self._opacity.setOpacity(0.0)
        anim = QPropertyAnimation(self._opacity, b"opacity", self)
        anim.setDuration(_ANIM_DURATION_MS)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        # Keep a reference so GC doesn't collect mid-animation.
        self._show_anim = anim

    def dismiss(self) -> None:
        """Fade out and remove this toast from its parent layout."""
        self._timer.stop()
        anim = QPropertyAnimation(self._opacity, b"opacity", self)
        anim.setDuration(_ANIM_DURATION_MS)
        anim.setStartValue(self._opacity.opacity())
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        anim.finished.connect(self._remove_self)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        self._hide_anim = anim

    def _remove_self(self) -> None:
        """Remove this widget from its parent layout and schedule deletion."""
        parent = self.parentWidget()
        layout = parent.layout() if parent is not None else None
        if layout is not None:
            try:
                layout.removeWidget(self)
            except Exception:
                logger.debug("Toast: could not remove widget from layout", exc_info=True)
        self.setParent(None)
        self.deleteLater()


class ToastManager:
    """Manages a stack of toasts within a container widget.

    Toasts are inserted into a QVBoxLayout owned by the container (typically
    the main window's central area, just above the status bar). Each new toast
    is added at the top of the stack.
    """

    def __init__(self, container: QWidget, layout: QVBoxLayout) -> None:
        self._container = container
        self._layout = layout
        self._toasts: list[ToastWidget] = []

    def show(
        self,
        message: str,
        *,
        kind: str = "info",
        duration_ms: int = _TOAST_DURATION_MS,
    ) -> ToastWidget:
        """Show a toast and return it. Callers may keep a reference to dismiss
        early if needed."""
        toast = ToastWidget(message, kind=kind, duration_ms=duration_ms, parent=self._container)
        # Insert at index 0 so newest toasts appear at the top.
        self._layout.insertWidget(0, toast)
        self._toasts.insert(0, toast)
        toast.show()
        # Clean up references when dismissed.
        toast.destroyed.connect(lambda _: self._drop(toast))
        return toast

    def success(self, message: str, duration_ms: int = _TOAST_DURATION_MS) -> ToastWidget:
        return self.show(message, kind="success", duration_ms=duration_ms)

    def warning(self, message: str, duration_ms: int = _TOAST_DURATION_MS) -> ToastWidget:
        return self.show(message, kind="warning", duration_ms=duration_ms)

    def error(self, message: str, duration_ms: int = _TOAST_DURATION_MS) -> ToastWidget:
        return self.show(message, kind="error", duration_ms=duration_ms)

    def info(self, message: str, duration_ms: int = _TOAST_DURATION_MS) -> ToastWidget:
        return self.show(message, kind="info", duration_ms=duration_ms)

    def clear(self) -> None:
        """Dismiss all visible toasts immediately."""
        for toast in list(self._toasts):
            toast.dismiss()

    def _drop(self, toast: ToastWidget) -> None:
        try:
            self._toasts.remove(toast)
        except ValueError:
            pass
