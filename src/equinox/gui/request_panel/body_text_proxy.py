import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class _NoopSignal:
    """Stand-in for a Qt signal when the underlying widget is unavailable.

    Exposes :meth:`connect` so call-sites that do
    ``proxy.textChanged.connect(slot)`` succeed without error.
    """

    def connect(self, slot) -> None:  # noqa: ARG002
        pass  # intentionally empty — nothing to wire up in fallback mode


class BodyTextProxy:
    """A resilient proxy for the JsonBodyEditor.

    Forwards calls to the underlying widget when available.  If the
    underlying C++ object has been deleted (as seen in some headless
    test environments), the proxy falls back to an in-memory text buffer
    so callers can still get/set body text and the panel can respond
    (mark dirty, update labels) without raising :exc:`RuntimeError`.

    Widget availability is checked lazily: a :exc:`RuntimeError` from a
    widget call (C++ object deleted) permanently nullifies ``_widget`` and
    switches all subsequent calls to the fallback path.
    """

    def __init__(self, panel, widget=None) -> None:
        self._panel = panel
        self._widget = widget
        self._buffer: str = ""
        # Cached QTextDocument used in fallback mode so syntax highlighters
        # always receive the same object reference (not a fresh one each call).
        self._fallback_doc: Optional[Any] = None

    # ── Internal helpers ─────────────────────────────────────────────

    def _fw(self, name: str, *args: Any, **kwargs: Any) -> Any:
        """Forward a named method call to the widget; nullify on RuntimeError.

        Returns the widget method's return value, or ``None`` when the widget
        is unavailable.  Suitable for pure pass-through void methods that have
        no fallback side-effects.
        """
        if self._widget is not None:
            try:
                return getattr(self._widget, name)(*args, **kwargs)
            except RuntimeError:
                self._widget = None
        return None

    def _panel_op(self, name: str) -> None:
        """Call a zero-argument method on the parent panel, swallowing all exceptions."""
        try:
            getattr(self._panel, name)()
        except Exception:
            logger.debug("BodyTextProxy: panel.%s() failed", name, exc_info=True)

    def _has_widget(self) -> bool:
        """Return ``True`` if the underlying C++ widget object is still alive."""
        return self._widget is not None

    # ── Signals ──────────────────────────────────────────────────────

    @property
    def textChanged(self):
        """Expose the underlying signal when available, otherwise a no-op.

        Allows callers that do ``proxy.textChanged.connect(slot)`` to work
        safely even after the C++ object is gone.
        """
        if self._widget is not None:
            try:
                return self._widget.textChanged
            except RuntimeError:
                self._widget = None
        return _NoopSignal()

    # ── Primary text interface ────────────────────────────────────────

    def setPlainText(self, text: str) -> None:
        if self._widget is not None:
            try:
                self._widget.setPlainText(text)
                return
            except RuntimeError:
                self._widget = None
        # Fallback — update buffer and keep the cached document in sync.
        self._buffer = text
        if self._fallback_doc is not None:
            self._fallback_doc.setPlainText(text)
        self._panel_op("_mark_dirty")
        self._panel_op("_update_tab_labels")

    def clear(self) -> None:
        if self._widget is not None:
            try:
                self._widget.clear()
                return
            except RuntimeError:
                self._widget = None
        # Fallback — mirror the side effects of setPlainText("") so the panel
        # stays in sync (dirty flag, tab labels) regardless of which path clears
        # the text.
        self._buffer = ""
        if self._fallback_doc is not None:
            self._fallback_doc.setPlainText("")
        self._panel_op("_mark_dirty")
        self._panel_op("_update_tab_labels")

    def toPlainText(self) -> str:
        if self._widget is not None:
            try:
                return self._widget.toPlainText()
            except RuntimeError:
                self._widget = None
        return self._buffer

    # ── Document ─────────────────────────────────────────────────────

    def document(self):
        """Return the widget's ``QTextDocument``, or a lazily-cached fallback.

        The fallback document is created once and reused so syntax highlighters
        always receive the same object reference instead of a fresh document on
        every call.
        """
        if self._widget is not None:
            try:
                return self._widget.document()
            except RuntimeError:
                self._widget = None
        if self._fallback_doc is None:
            from PyQt6.QtGui import QTextDocument
            self._fallback_doc = QTextDocument()
            self._fallback_doc.setPlainText(self._buffer)
        return self._fallback_doc

    # ── Visibility / appearance pass-throughs ─────────────────────────

    def setVisible(self, v: bool) -> None:
        self._fw("setVisible", v)

    def setEnabled(self, v: bool) -> None:
        self._fw("setEnabled", v)

    def setPlaceholderText(self, txt: str) -> None:
        self._fw("setPlaceholderText", txt)

    def setFont(self, font) -> None:
        self._fw("setFont", font)

    # ── Dynamic forwarding for all other attributes ───────────────────

    def __getattr__(self, name: str) -> Any:
        """Forward any unrecognised attribute lookup to the underlying widget."""
        if self._widget is not None:
            try:
                return getattr(self._widget, name)
            except RuntimeError:
                self._widget = None
        raise AttributeError(
            f"{type(self).__name__!r} has no attribute {name!r}"
            " (underlying widget is unavailable)"
        )

    def __repr__(self) -> str:
        status = "live" if self._widget is not None else "fallback"
        preview = repr(self._buffer[:37] + "...") if len(self._buffer) > 40 else repr(self._buffer)
        return f"<BodyTextProxy [{status}] buffer={preview}>"
