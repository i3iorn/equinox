"""QMessageBox subclass with a built-in *Copy* button."""
from __future__ import annotations

import logging

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication
from PyQt6.QtWidgets import QMessageBox
from PyQt6.QtWidgets import QPushButton
from PyQt6.QtWidgets import QWidget

logger = logging.getLogger(__name__)


class CopyableMessageBox(QMessageBox):
    """A ``QMessageBox`` that always includes **OK** and **Copy** buttons.

    *copy_text* is the string placed on the clipboard when the user clicks
    **Copy**.  If omitted it defaults to the *text* (main message body).

    The dialog stays open after **Copy** is clicked (no close/reopen flash).
    The button label briefly changes to **"Copied ✓"** as visual confirmation,
    then resets automatically.

    Usage::

        CopyableMessageBox.critical(parent, "Title", "Visible message",
                                     copy_text=full_traceback)
    """

    _COPY_LABEL = "Copy"
    _COPIED_LABEL = "Copied \u2713"
    _COPIED_RESET_MS = 1500  # ms before the button label reverts

    def __init__(
        self,
        icon: QMessageBox.Icon,
        title: str,
        text: str,
        copy_text: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(icon, title, text, QMessageBox.StandardButton.Ok, parent)
        # Explicit None check: an empty-string copy_text means "copy nothing",
        # not "fall back to the visible message".
        self._copy_text = copy_text if copy_text is not None else text
        self._copy_btn: QPushButton = self.addButton(
            self._COPY_LABEL, QMessageBox.ButtonRole.ActionRole,
        )  # type: ignore[assignment]

        # Single reusable timer so repeated Copy clicks just restart the countdown.
        self._reset_timer = QTimer(self)
        self._reset_timer.setSingleShot(True)
        self._reset_timer.setInterval(self._COPIED_RESET_MS)
        self._reset_timer.timeout.connect(self._reset_copy_label)

    def done(self, result: int) -> None:
        """Intercept the automatic close when Copy is clicked.

        ``QMessageBox`` calls ``done()`` for every button click.  When the
        Copy button is the trigger we copy text and return early — leaving the
        dialog open — instead of forwarding to ``super().done()``.
        """
        if self.clickedButton() is self._copy_btn:
            self._do_copy()
            return  # keep the dialog open; do NOT call super().done()
        super().done(result)

    # ── Internal helpers ──────────────────────────────────────────────

    def _do_copy(self) -> None:
        """Write *copy_text* to the system clipboard with error handling."""
        clipboard = QApplication.clipboard()
        if clipboard is None:
            logger.warning("System clipboard is unavailable; cannot copy text")
            return
        try:
            clipboard.setText(self._copy_text)
        except Exception:
            logger.warning("Failed to write text to clipboard", exc_info=True)
            return
        # Visual confirmation: rename the button, then restore after a short delay.
        self._copy_btn.setText(self._COPIED_LABEL)
        self._reset_timer.start()  # restart if already running

    def _reset_copy_label(self) -> None:
        self._copy_btn.setText(self._COPY_LABEL)

    @classmethod
    def _show(
        cls,
        icon: QMessageBox.Icon,
        parent: QWidget | None,
        title: str,
        text: str,
        copy_text: str | None,
    ) -> int:
        """Instantiate and execute the dialog with the given *icon*."""
        return cls(icon, title, text, copy_text, parent).exec()

    # ── Convenience class methods (mirror QMessageBox API) ────────────

    @classmethod
    def critical( # type: ignore[override]
        cls,
        parent: QWidget | None,
        title: str,
        text: str,
        copy_text: str | None = None,
    ) -> int:
        """Show a *Critical* dialog with OK + Copy buttons."""
        return cls._show(QMessageBox.Icon.Critical, parent, title, text, copy_text)

    @classmethod
    def warning( # type: ignore[override]
        cls,
        parent: QWidget | None,
        title: str,
        text: str,
        copy_text: str | None = None,
    ) -> int:
        """Show a *Warning* dialog with OK + Copy buttons."""
        return cls._show(QMessageBox.Icon.Warning, parent, title, text, copy_text)

    @classmethod
    def information( # type: ignore[override]
        cls,
        parent: QWidget | None,
        title: str,
        text: str,
        copy_text: str | None = None,
    ) -> int:
        """Show an *Information* dialog with OK + Copy buttons."""
        return cls._show(QMessageBox.Icon.Information, parent, title, text, copy_text)
