"""Centralized GUI error presentation helpers.

GUI modules should call this presenter instead of formatting raw ``QMessageBox``
content ad hoc. The API is intentionally small and uses consistent dialog
headings, optional log-file hints, and copyable technical details.
"""

from __future__ import annotations

import logging
from typing import cast

from PyQt6.QtWidgets import QMessageBox, QWidget

from equinox.gui.ui_common import confirm_yes_no
from equinox.gui.widgets import CopyableMessageBox

logger = logging.getLogger(__name__)


class ErrorPresenter:
    """Tiny facade for warning/error/info/confirm dialogs and request-failure UX."""

    TITLE_WARNING = "Warning"
    TITLE_ERROR = "Error"
    TITLE_INFO = "Information"
    TITLE_CONFIRM = "Confirm"
    TITLE_REQUEST_FAILED = "Request Failed"

    @staticmethod
    def warning(
        parent: QWidget | None,
        message: str,
        *,
        title: str | None = None,
        details: str | None = None,
    ) -> None:
        if details:
            CopyableMessageBox.warning(
                parent,
                title or ErrorPresenter.TITLE_WARNING,
                message,
                copy_text=details,
            )
            return
        QMessageBox.warning(parent, title or ErrorPresenter.TITLE_WARNING, message)

    @staticmethod
    def error(
        parent: QWidget | None,
        message: str,
        *,
        title: str | None = None,
        details: str | None = None,
    ) -> None:
        logger.error(
            "%s: %s%s",
            title or ErrorPresenter.TITLE_ERROR,
            message,
            f" ({details})" if details else "",
        )
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
    def info(
        parent: QWidget | None,
        message: str,
        *,
        title: str | None = None,
        details: str | None = None,
    ) -> None:
        if details:
            CopyableMessageBox.information(
                parent,
                title or ErrorPresenter.TITLE_INFO,
                message,
                copy_text=details,
            )
            return
        QMessageBox.information(parent, title or ErrorPresenter.TITLE_INFO, message)

    @staticmethod
    def confirm(
        parent: QWidget | None,
        message: str,
        *,
        title: str | None = None,
        default_no: bool = False,
    ) -> bool:
        """Ask a yes/no question. Returns True only when the user picks Yes.

        Companion to warning/error/info so callers have a single presenter for
        the whole warning/error/info/confirm family. Delegates to
        ``ui_common.confirm_yes_no`` rather than repeating it, so the two
        entry points can never answer the same question differently.
        """
        return confirm_yes_no(
            cast(QWidget, parent),
            title or ErrorPresenter.TITLE_CONFIRM,
            message,
            default_no=default_no,
        )

    @staticmethod
    def request_failure(
        parent: QWidget | None,
        *,
        exc_type: str,
        message: str,
        hint: str | None = None,
        details: str | None = None,
        log_file_path: str | None = None,
    ) -> None:
        logger.error("Request failed (%s): %s", exc_type, message)
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
    def show_status(parent: QWidget | None, message: str, *, timeout_ms: int = 5000) -> None:
        if parent is None:
            return
        try:
            window = parent.window()
            status_bar = getattr(window, "statusBar", None)
            if callable(status_bar):
                status_bar().showMessage(message, timeout_ms)
        except Exception:
            return
