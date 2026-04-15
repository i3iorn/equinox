"""Resilient proxy for JsonBodyEditor widget.

Handles cases where the underlying PyQt6 widget may be unavailable (deleted C++ object),
which can occur in headless and test environments. Falls back to an in-memory text buffer
while maintaining the same API surface so callers are unaffected.

The proxy:
- Forwards all calls to the real widget when available
- Falls back to in-memory buffer when widget is gone
- Maintains QTextDocument reference for syntax highlighters
- Notifies parent panel of state changes
"""

from __future__ import annotations

import logging
from typing import Any, Optional, Callable

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────────────────────────────────────

# Attributes that live on the proxy itself (not forwarded to the widget)
# Used by __getattr__ to avoid infinite recursion during __init__
_PROXY_ATTRS = frozenset({"_panel", "_widget", "_buffer", "_fallback_doc"})

# Panel operations that notify parent of state changes
_PANEL_NOTIFY_OPS = frozenset({"_mark_dirty", "_update_tab_labels"})

# Buffer preview length for repr()
_BUFFER_PREVIEW_LENGTH = 37
_BUFFER_PREVIEW_LIMIT = 40


class _NoopSignal:
    """Stand-in for Qt signal when widget is unavailable.

    Exposes connect() method so call-sites that do:
    ``proxy.textChanged.connect(slot)``

    succeed without error even after widget is deleted.
    """

    def connect(self, slot: Callable[[Any], None]) -> None:
        """Silently accept signal connection (no-op in fallback mode).

        Args:
            slot: Signal slot (ignored)
        """
        pass  # Intentionally empty — nothing to wire in fallback mode


class BodyTextProxy:
    """Resilient proxy for JsonBodyEditor handling headless environments.

    Forwards calls to the underlying widget when available. Falls back to
    an in-memory text buffer if the underlying C++ object has been deleted
    (common in headless/test environments).

    Features:
    - Lazy widget availability checking
    - Automatic fallback on RuntimeError
    - Cached QTextDocument for syntax highlighters
    - Parent panel notification on state changes
    - Complete API compatibility with JsonBodyEditor

    Usage:
    ```python
    # Create proxy with optional widget
    proxy = BodyTextProxy(panel, widget=editor)

    # Use like a normal widget
    proxy.setPlainText("code here")
    text = proxy.toPlainText()

    # Works even if widget becomes unavailable
    # - Switches to fallback automatically
    # - Parent panel stays in sync
    ```
    """

    def __init__(self, panel: Any, widget: Optional[Any] = None) -> None:
        """Initialize proxy.

        Args:
            panel: Parent panel (used for notifications)
            widget: JsonBodyEditor widget (optional, can be None)
        """
        self._panel = panel
        self._widget = widget
        self._buffer: str = ""
        # Cached QTextDocument for fallback mode so syntax highlighters
        # always receive the same object reference
        self._fallback_doc: Optional[Any] = None

    # ── Internal helpers ──────────────────────────────────────────────

    def _invalidate(self) -> None:
        """Mark underlying C++ widget as deleted.

        Switches all subsequent calls to fallback buffer mode.
        """
        logger.debug("BodyTextProxy: underlying widget invalidated, switching to fallback")
        self._widget = None

    def _has_widget(self) -> bool:
        """Check if underlying C++ widget is still alive.

        Returns:
            True if widget is available, False otherwise
        """
        return self._widget is not None

    def _fw(self, name: str, *args: Any, **kwargs: Any) -> Any:
        """Forward method call to widget; invalidate on RuntimeError.

        Attempts to call a named method on the underlying widget.
        If widget is unavailable or raises RuntimeError (C++ object deleted),
        invalidates widget and returns None.

        Args:
            name: Method name to call
            *args: Positional arguments
            **kwargs: Keyword arguments

        Returns:
            Widget method return value, or None if unavailable
        """
        if self._widget is not None:
            try:
                return getattr(self._widget, name)(*args, **kwargs)
            except RuntimeError as e:
                logger.debug("BodyTextProxy: RuntimeError forwarding %s, invalidating widget: %s", name, e)
                self._invalidate()
        return None

    def _fallback_set(self, text: str) -> None:
        """Update in-memory buffer and notify parent panel.

        Shared by setPlainText() and clear() to avoid code duplication.
        Also updates the fallback QTextDocument so syntax highlighters
        see the latest content.

        Args:
            text: New text to store in buffer
        """
        self._buffer = text

        # Update fallback document if it exists
        if self._fallback_doc is not None:
            try:
                self._fallback_doc.setPlainText(text)
            except Exception as e:
                logger.debug("BodyTextProxy: failed to update fallback document: %s", e)

        # Notify parent panel of state change
        self._notify_panel("_mark_dirty")
        self._notify_panel("_update_tab_labels")

    def _notify_panel(self, operation: str) -> None:
        """Call a zero-argument method on parent panel, swallowing exceptions.

        Used to notify parent panel of state changes (mark dirty, update labels).
        Exceptions are logged but not raised to avoid cascading failures.

        Args:
            operation: Method name to call on panel (e.g., "_mark_dirty")
        """
        if operation not in _PANEL_NOTIFY_OPS:
            logger.warning("BodyTextProxy: unexpected panel operation: %s", operation)
            return

        try:
            getattr(self._panel, operation)()
        except Exception as e:
            logger.debug("BodyTextProxy: panel.%s() failed: %s", operation, e)

    def _get_or_create_fallback_doc(self) -> Any:
        """Get or create the fallback QTextDocument.

        Creates document once and caches it so syntax highlighters always
        receive the same object reference instead of fresh documents.

        Returns:
            Cached QTextDocument
        """
        if self._fallback_doc is None:
            try:
                from PyQt6.QtGui import QTextDocument
                self._fallback_doc = QTextDocument()
                self._fallback_doc.setPlainText(self._buffer)
                logger.debug("BodyTextProxy: created fallback QTextDocument")
            except Exception as e:
                logger.error("BodyTextProxy: failed to create fallback document: %s", e)
                # Create a minimal fallback
                self._fallback_doc = None

        return self._fallback_doc

    # ── Signals ──────────────────────────────────────────────────────

    @property
    def textChanged(self) -> Any:
        """Expose underlying signal or no-op stand-in.

        Allows callers that do ``proxy.textChanged.connect(slot)`` to work
        safely even after the C++ widget is deleted.

        Returns:
            Widget's textChanged signal or _NoopSignal instance
        """
        if self._widget is not None:
            try:
                return self._widget.textChanged
            except RuntimeError as e:
                logger.debug("BodyTextProxy: RuntimeError accessing textChanged signal: %s", e)
                self._invalidate()

        return _NoopSignal()

    # ── Primary text interface ────────────────────────────────────────

    def setPlainText(self, text: str) -> None:
        """Set editor content.

        Uses widget if available, otherwise falls back to in-memory buffer.

        Args:
            text: Plain text to set (str or None)
        """
        if text is None:
            text = ""

        if self._widget is not None:
            try:
                self._widget.setPlainText(text)
                return
            except RuntimeError as e:
                logger.debug("BodyTextProxy: RuntimeError in setPlainText: %s", e)
                self._invalidate()

        self._fallback_set(text)

    def clear(self) -> None:
        """Clear editor content.

        Uses widget if available, otherwise falls back to clearing buffer.
        """
        if self._widget is not None:
            try:
                self._widget.clear()
                return
            except RuntimeError as e:
                logger.debug("BodyTextProxy: RuntimeError in clear: %s", e)
                self._invalidate()

        self._fallback_set("")

    def toPlainText(self) -> str:
        """Get editor content as plain text.

        Returns:
            Current text from widget or buffer
        """
        if self._widget is not None:
            try:
                return self._widget.toPlainText()
            except RuntimeError as e:
                logger.debug("BodyTextProxy: RuntimeError in toPlainText: %s", e)
                self._invalidate()

        return self._buffer

    # ── Document ─────────────────────────────────────────────────────

    def document(self) -> Any:
        """Get QTextDocument for syntax highlighting.

        Returns widget's document if available, otherwise returns cached
        fallback document (same reference on every call).

        Returns:
            QTextDocument instance
        """
        if self._widget is not None:
            try:
                return self._widget.document()
            except RuntimeError as e:
                logger.debug("BodyTextProxy: RuntimeError accessing document: %s", e)
                self._invalidate()

        return self._get_or_create_fallback_doc()

    # ── Appearance and behavior pass-throughs ──────────────────────────

    def setVisible(self, visible: bool) -> None:
        """Set widget visibility.

        Args:
            visible: Whether to show widget
        """
        self._fw("setVisible", visible)

    def setEnabled(self, enabled: bool) -> None:
        """Set widget enabled state.

        Args:
            enabled: Whether to enable widget
        """
        self._fw("setEnabled", enabled)

    def setPlaceholderText(self, text: str) -> None:
        """Set placeholder text.

        Args:
            text: Placeholder text to display
        """
        self._fw("setPlaceholderText", text)

    def setFont(self, font: Any) -> None:
        """Set widget font.

        Args:
            font: QFont instance
        """
        self._fw("setFont", font)

    # ── Dynamic forwarding for all other attributes ───────────────────

    def __getattr__(self, name: str) -> Any:
        """Forward unrecognised attributes to underlying widget.

        The _PROXY_ATTRS guard prevents infinite recursion when Python
        looks up instance attributes before __init__ is complete.

        Args:
            name: Attribute name

        Returns:
            Attribute value from widget

        Raises:
            AttributeError: If attribute not found and widget unavailable
        """
        if name in _PROXY_ATTRS:
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")

        widget = self.__dict__.get("_widget")
        if widget is not None:
            try:
                return getattr(widget, name)
            except RuntimeError as e:
                logger.debug("BodyTextProxy: RuntimeError accessing %s: %s", name, e)
                self._widget = None

        raise AttributeError(
            f"'{type(self).__name__}' has no attribute '{name}' "
            "(underlying widget is unavailable)"
        )

    def __bool__(self) -> bool:
        """Always return True for truthiness checks.

        The proxy itself is always available even if the underlying widget is not.

        Returns:
            True
        """
        return True

    def __repr__(self) -> str:
        """Return diagnostic string representation.

        Includes widget status and buffer content preview.

        Returns:
            String like "<BodyTextProxy [live] buffer='code here'>"
        """
        status = "live" if self._widget is not None else "fallback"
        buf = self._buffer or ""

        if len(buf) > _BUFFER_PREVIEW_LIMIT:
            preview = repr(buf[:_BUFFER_PREVIEW_LENGTH] + "...")
        else:
            preview = repr(buf)

        return f"<BodyTextProxy [{status}] buffer={preview}>"
