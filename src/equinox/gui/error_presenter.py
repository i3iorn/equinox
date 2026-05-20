"""Centralized GUI error presentation helpers.

GUI modules should call this presenter instead of formatting raw ``QMessageBox``
content ad hoc. The API is intentionally small and uses consistent dialog
headings, optional log-file hints, and copyable technical details.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtWidgets import QMessageBox, QWidget

from equinox.gui.widgets import CopyableMessageBox


class ErrorPresenter:
    """Tiny facade for warning/error/info dialogs and request-failure UX."""

    TITLE_WARNING = "Warning"
    TITLE_ERROR = "Error"
    TITLE_INFO = "Information"
    TITLE_REQUEST_FAILED = "Request Failed"

    @staticmethod
    def warning(parent: Optional[QWidget], message: str, *, title: Optional[str] = None) -> None:
        QMessageBox.warning(parent, title or ErrorPresenter.TITLE_WARNING, message)

    @staticmethod
    def error(
        parent: Optional[QWidget],
        message: str,
        *,
        title: Optional[str] = None,
        details: Optional[str] = None,
    ) -> None:
        if details:
            CopyableMessageBox.critical(
                parent,
                title or ErrorPresenter.TITLE_ERROR,
                message,
                copy_text=details,
            )
            return
        QMessageBox.critical(parent, title or ErrorPresenter.TITLE_ERROR, message)

    @staticmethod
    def info(parent: Optional[QWidget], message: str, *, title: Optional[str] = None) -> None:
        QMessageBox.information(parent, title or ErrorPresenter.TITLE_INFO, message)

    @staticmethod
    def request_failure(
        parent: Optional[QWidget],
        *,
        exc_type: str,
        message: str,
        hint: Optional[str] = None,
        details: Optional[str] = None,
        log_file_path: Optional[str] = None,
    ) -> None:
        log_hint = f"\n\nFull details in: {log_file_path}" if log_file_path else ""
        dialog_text = f"{message}{log_hint}"
        if hint:
            dialog_text = f"{message}\n\n{hint}{log_hint}"

        title = f"{ErrorPresenter.TITLE_REQUEST_FAILED} - {exc_type}"
        CopyableMessageBox.critical(
            parent,
            title,
            dialog_text,
            copy_text=details or message,
        )

    @staticmethod
    def show_status(parent: Optional[QWidget], message: str, *, timeout_ms: int = 5000) -> None:
        if parent is None:
            return
        try:
            window = parent.window()
            status_bar = getattr(window, "statusBar", None)
            if callable(status_bar):
                status_bar().showMessage(message, timeout_ms)
        except Exception:
            return

