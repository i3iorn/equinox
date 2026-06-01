"""Worker-dispatch and send-state helpers for ``RequestPanel``."""
from __future__ import annotations

import logging
from typing import Any
from typing import cast
from typing import TYPE_CHECKING

from equinox.gui.error_presenter import ErrorPresenter
from equinox.gui.request_panel._constants import PREFLIGHT_SEPARATOR
from equinox.gui.request_panel._constants import STATUS_DURATION_SHORT
from equinox.gui.request_panel._constants import WORKER_WAIT_MS
from equinox.gui.workers import RequestWorker
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QWidget

logger = logging.getLogger(__name__)

_MSG_MISSING_URL = "Please enter a request URL."
_MSG_CANCELLED = "Request cancelled"


class SendWorkerMixin:
    """Manage worker dispatch, preflight banner rendering, and send UI state."""

    _worker: RequestWorker | None
    _session_vars: dict[str, str]
    session_vars_changed: Any
    _cookie_manager: Any
    _preflight_label: Any
    _preflight_banner: Any
    send_button: Any
    url_input: Any
    method_combo: Any
    cancel_button: Any
    _elapsed_timer: QTimer
    _elapsed_secs: float

    if TYPE_CHECKING:

        def _run_preflight_checks(self) -> list[str]: ...
        def _handle_response(self, result: object, worker: Any) -> None: ...
        def _status_message(self, message: str, timeout_ms: int = ...) -> None: ...

    def _as_qwidget(self) -> QWidget:
        return cast(QWidget, cast(object, self))

    def _ensure_sendable_url(self, url: str) -> bool:
        """Warn and abort when the request URL is empty."""
        if url:
            return True
        ErrorPresenter.warning(self._as_qwidget(), _MSG_MISSING_URL, title="Missing URL")
        return False

    def _display_preflight_warnings(self) -> None:
        """Show or hide the preflight warning banner."""
        warnings = self._run_preflight_checks()
        if warnings:
            self._preflight_label.setText(PREFLIGHT_SEPARATOR.join(warnings))
            self._preflight_banner.setVisible(True)
            logger.debug("Preflight warnings: %s", warnings)
            return
        self._preflight_banner.setVisible(False)

    @staticmethod
    def _resolve_proxy_url() -> str | None:
        """Resolve proxy settings on the main thread."""
        from equinox.gui.ui_common import resolve_proxy_url

        result = resolve_proxy_url(logger=logger)
        return str(result) if result is not None else None

    @staticmethod
    def _display_script_result(label: Any, result: Any) -> None:
        """Render a pre/post script result label."""
        label.setObjectName("script-result-error" if result.error else "script-result-ok")
        if result.error:
            label.setText(f"Error: {result.error}")
            return
        change_count = len(result.env_changes)
        label.setText(f"OK ÔÇö {change_count} var(s) set" if change_count else "OK")

    def _apply_script_vars(self, result: Any) -> None:
        """Merge script output variables into session state."""
        if result.error or not result.env_changes:
            return
        self._session_vars.update(result.env_changes)
        self.session_vars_changed.emit(dict(self._session_vars))

    def _dispatch_worker(self, request: Any) -> None:
        """Create and start the background request worker."""
        proxy = self._resolve_proxy_url()
        self._worker = RequestWorker(
            request,
            self,
            cookie_manager=self._cookie_manager,
            proxy=proxy,
        )
        worker_ref = self._worker
        self._worker.finished.connect(lambda result, w=worker_ref: self._handle_response(result, w))
        self._worker.start()

    def _cancel_request(self) -> None:
        """Cancel the active worker and reset the send state."""
        worker = self._worker
        if worker is not None:
            worker.cancel()
            worker.wait(WORKER_WAIT_MS)
            self._worker = None
        self._set_sending_state(False)
        self._status_message(_MSG_CANCELLED, STATUS_DURATION_SHORT)

    def _defer_task(self, fn: Any, *args: Any, **kwargs: Any) -> None:
        """Schedule a follow-up task on the next Qt event-loop turn."""
        QTimer.singleShot(0, lambda: fn(*args, **kwargs))

    def _normalize_exception(self, result: object) -> object:
        """Convert arbitrary exceptions into ``RichError`` safely."""
        from equinox.core.format.error_enrichment import RichError, enrich_exception

        if isinstance(result, Exception) and not isinstance(result, RichError):
            try:
                return enrich_exception(result)
            except Exception:
                return RichError(exc_type=type(result).__name__, message=str(result) or "Unknown error", tb="")
        return result

    def _set_sending_state(self, sending: bool) -> None:
        """Toggle the request editor between idle and sending states."""
        enabled = not sending
        self.send_button.setEnabled(enabled)
        self.url_input.setEnabled(enabled)
        self.method_combo.setEnabled(enabled)
        self.cancel_button.setVisible(sending)
        if sending:
            self._elapsed_secs = 0.0
            self._elapsed_timer.start()
            self.send_button.setText("0.0sÔÇª")
            return
        self._elapsed_timer.stop()
        self.send_button.setText("Send")

    def _tick_elapsed(self) -> None:
        """Update the elapsed-time indicator while a request is in flight."""
        self._elapsed_secs += 0.1
        self.send_button.setText(f"{self._elapsed_secs:.1f}sÔÇª")
