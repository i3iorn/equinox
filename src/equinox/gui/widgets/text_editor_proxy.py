"""Resilient text-editor proxy for PyQt text widgets.

Falls back to an in-memory buffer when the wrapped C++ widget becomes
unavailable, which keeps higher-level GUI code working in tests and teardown
paths.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_PROXY_ATTRS = frozenset({"_panel", "_widget", "_buffer", "_fallback_doc"})
_PANEL_NOTIFY_OPS = frozenset({"_mark_dirty", "_update_tab_labels"})
_BUFFER_PREVIEW_LENGTH = 37
_BUFFER_PREVIEW_LIMIT = 40


class _NoopSignal:
    """Stand-in signal used when the wrapped widget is unavailable."""

    def connect(self, _slot: Any) -> None:
        """Accept connections without wiring a real signal."""


class TextEditorProxy:
    """Resilient proxy for text editors used by GUI panels."""

    def __init__(self, panel: Any, widget: Any | None = None) -> None:
        self._panel = panel
        self._widget = widget
        self._buffer = ""
        self._fallback_doc: Any | None = None

    def _invalidate(self) -> None:
        """Mark the underlying widget as unavailable."""
        logger.debug("TextEditorProxy: widget invalidated; switching to fallback")
        self._widget = None

    def _has_widget(self) -> bool:
        """Return ``True`` when a wrapped editor widget is still available."""
        return self._widget is not None

    def _fw(self, name: str, *args: Any, **kwargs: Any) -> Any:
        """Forward a call to the wrapped widget when available."""
        if self._widget is None:
            return None
        try:
            return getattr(self._widget, name)(*args, **kwargs)
        except RuntimeError as exc:
            logger.debug("TextEditorProxy: %s failed; invalidating widget: %s", name, exc)
            self._invalidate()
            return None

    def _notify_panel(self, operation: str) -> None:
        """Call a known zero-argument panel hook safely."""
        if operation not in _PANEL_NOTIFY_OPS:
            logger.warning("TextEditorProxy: unexpected panel operation %s", operation)
            return
        try:
            getattr(self._panel, operation)()
        except Exception as exc:
            logger.debug("TextEditorProxy: panel.%s() failed: %s", operation, exc)

    def _sync_fallback_document(self, text: str) -> None:
        """Keep the cached fallback document aligned with the buffer."""
        if self._fallback_doc is None:
            return
        try:
            self._fallback_doc.setPlainText(text)
        except Exception as exc:
            logger.debug("TextEditorProxy: failed to sync fallback document: %s", exc)

    def _fallback_set(self, text: str) -> None:
        """Update fallback text state and notify the owning panel."""
        self._buffer = text
        self._sync_fallback_document(text)
        self._notify_panel("_mark_dirty")
        self._notify_panel("_update_tab_labels")

    def _get_or_create_fallback_doc(self) -> Any:
        """Return a cached ``QTextDocument`` for fallback mode."""
        if self._fallback_doc is not None:
            return self._fallback_doc
        try:
            from PyQt6.QtGui import QTextDocument

            self._fallback_doc = QTextDocument()
            self._fallback_doc.setPlainText(self._buffer)
            logger.debug("TextEditorProxy: created fallback QTextDocument")
        except Exception as exc:
            logger.error("TextEditorProxy: failed to create fallback document: %s", exc)
            self._fallback_doc = None
        return self._fallback_doc

    @property
    def textChanged(self) -> Any:
        """Expose the wrapped signal or a no-op replacement."""
        if self._widget is not None:
            try:
                return self._widget.textChanged
            except RuntimeError as exc:
                logger.debug("TextEditorProxy: textChanged unavailable: %s", exc)
                self._invalidate()
        return _NoopSignal()

    def setPlainText(self, text: str | None) -> None:
        """Set editor text on the widget or fallback buffer."""
        normalized = "" if text is None else text
        if self._widget is not None:
            try:
                self._widget.setPlainText(normalized)
                return
            except RuntimeError as exc:
                logger.debug("TextEditorProxy: setPlainText failed: %s", exc)
                self._invalidate()
        self._fallback_set(normalized)

    def clear(self) -> None:
        """Clear editor text on the widget or fallback buffer."""
        if self._widget is not None:
            try:
                self._widget.clear()
                return
            except RuntimeError as exc:
                logger.debug("TextEditorProxy: clear failed: %s", exc)
                self._invalidate()
        self._fallback_set("")

    def toPlainText(self) -> str:
        """Return editor text from the widget or fallback buffer."""
        if self._widget is not None:
            try:
                return str(self._widget.toPlainText())
            except RuntimeError as exc:
                logger.debug("TextEditorProxy: toPlainText failed: %s", exc)
                self._invalidate()
        return self._buffer

    def document(self) -> Any:
        """Return the wrapped document or the cached fallback document."""
        if self._widget is not None:
            try:
                return self._widget.document()
            except RuntimeError as exc:
                logger.debug("TextEditorProxy: document unavailable: %s", exc)
                self._invalidate()
        return self._get_or_create_fallback_doc()

    def setVisible(self, visible: bool) -> None:
        """Forward visibility changes to the wrapped widget."""
        self._fw("setVisible", visible)

    def setEnabled(self, enabled: bool) -> None:
        """Forward enabled-state changes to the wrapped widget."""
        self._fw("setEnabled", enabled)

    def setPlaceholderText(self, text: str) -> None:
        """Forward placeholder text updates to the wrapped widget."""
        self._fw("setPlaceholderText", text)

    def setFont(self, font: Any) -> None:
        """Forward font updates to the wrapped widget."""
        self._fw("setFont", font)

    def __getattr__(self, name: str) -> Any:
        """Forward unknown attributes to the wrapped widget when possible."""
        if name in _PROXY_ATTRS:
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")
        widget = self.__dict__.get("_widget")
        if widget is not None:
            try:
                return getattr(widget, name)
            except RuntimeError as exc:
                logger.debug("TextEditorProxy: %s unavailable: %s", name, exc)
                self._widget = None
        raise AttributeError(
            f"'{type(self).__name__}' has no attribute '{name}' (underlying widget is unavailable)",
        )

    def __bool__(self) -> bool:
        """The proxy object itself is always available."""
        return True

    def __repr__(self) -> str:
        """Return a concise diagnostic representation."""
        status = "live" if self._widget is not None else "fallback"
        buffer_text = self._buffer or ""
        if len(buffer_text) > _BUFFER_PREVIEW_LIMIT:
            preview = repr(buffer_text[:_BUFFER_PREVIEW_LENGTH] + "...")
        else:
            preview = repr(buffer_text)
        return f"<TextEditorProxy [{status}] buffer={preview}>"
