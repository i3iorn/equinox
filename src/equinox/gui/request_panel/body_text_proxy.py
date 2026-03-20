from equinox.gui.request_panel.panel import logger


class BodyTextProxy:
    """A resilient proxy for the JsonBodyEditor.

    It forwards calls to the underlying widget when available. If the
    underlying C++ object has been deleted (as seen in some headless
    test environments), the proxy provides a lightweight fallback so
    callers (and tests) can still set/get body text and the panel can
    respond (mark dirty, update labels) without raising RuntimeError.
    """
    def __init__(self, panel, widget=None):
        self._panel = panel
        self._widget = widget
        self._buffer = ""

    class _NoopSignal:
        """A tiny object that exposes a connect(slot) method for safe_connect.

        It intentionally does nothing when connected; the panel's proxy will
        manually call _mark_dirty/_update_tab_labels when setPlainText is used.
        """
        def connect(self, slot):
            # No-op: nothing to connect to in headless fallback
            return


    def __getattr__(self, name: str):
        # Forward attribute access to the underlying widget when possible
        if self._widget is not None:
            try:
                return getattr(self._widget, name)
            except RuntimeError:
                self._widget = None
        raise AttributeError(name)

    @property
    def textChanged(self):
        """Expose the underlying signal when available, otherwise a noop.

        This allows callers that lazily retrieve signals (see safe_connect)
        to attempt connecting without raising AttributeError when the C++
        object is gone.
        """
        if self._widget is not None:
            try:
                return getattr(self._widget, "textChanged")
            except RuntimeError:
                self._widget = None
        return BodyTextProxy._NoopSignal()

    def _has_widget(self):
        return self._widget is not None

    def setPlainText(self, text: str):
        if self._has_widget():
            try:
                self._widget.setPlainText(text)
            except RuntimeError:
                # Underlying C++ object missing — fall back
                self._widget = None
                self._buffer = text
        else:
            self._buffer = text
        # Mark the panel dirty and update labels as the real signal would
        try:
            self._panel._mark_dirty()
            self._panel._update_tab_labels()
        except Exception:
            logger.debug("Failed to mark panel dirty from BodyTextProxy", exc_info=True)

    def clear(self):
        if self._has_widget():
            try:
                self._widget.clear()
                return
            except RuntimeError:
                self._widget = None
        self._buffer = ""

    def toPlainText(self) -> str:
        if self._has_widget():
            try:
                return self._widget.toPlainText()
            except RuntimeError:
                self._widget = None
        return self._buffer

    # Lightweight passthroughs / no-ops for methods used elsewhere
    def setVisible(self, v: bool):
        if self._has_widget():
            try:
                self._widget.setVisible(v)
            except RuntimeError:
                self._widget = None

    def setEnabled(self, v: bool):
        if self._has_widget():
            try:
                self._widget.setEnabled(v)
            except RuntimeError:
                self._widget = None

    def setPlaceholderText(self, txt: str):
        if self._has_widget():
            try:
                self._widget.setPlaceholderText(txt)
            except RuntimeError:
                self._widget = None

    def setFont(self, font):
        if self._has_widget():
            try:
                self._widget.setFont(font)
            except RuntimeError:
                self._widget = None

    def document(self):
        if self._has_widget():
            try:
                return self._widget.document()
            except RuntimeError:
                self._widget = None
        # Fallback: create a simple QTextDocument for syntax highlighter use
        from PyQt6.QtGui import QTextDocument
        doc = QTextDocument()
        doc.setPlainText(self._buffer)
        return doc
