"""Response-handling helpers for ``RequestPanel`` send flows."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

from PyQt6.QtWidgets import QWidget

from equinox.application.requests import (
    apply_captures,
    build_error_handling_plan,
    build_success_handling_plan,
    run_post_script,
)
from equinox.core.log_setup import get_log_file
from equinox.gui.error_presenter import ErrorPresenter
from equinox.gui.logging_utils import notify_log_panel
from equinox.gui.request_panel._constants import STATUS_DURATION_LONG

logger = logging.getLogger(__name__)

_URL_LOG_LIMIT = 80
_URL_ERROR_LOG_LIMIT = 80


class SendResponseMixin:
    """Route worker results into success and error handling flows."""

    _worker: Any | None = None
    _auth: Any | None
    _session_vars: dict[str, str]
    session_vars_changed: Any
    captures_results_label: Any
    post_script_editor: Any
    post_script_result: Any
    response_received: Any
    _request_history: Any
    _request_persistence: Any
    current_request: Any
    _logging_panel: Any

    if TYPE_CHECKING:

        def _set_sending_state(self, sending: bool) -> None: ...
        def _normalize_exception(self, result: object) -> object: ...
        def _status_message(self, message: str, timeout_ms: int = ...) -> None: ...
        def _evaluate_assertions(self, response: Any) -> None: ...
        def _add_url_to_completer(self, url: str) -> None: ...
        def _update_auth_display(self, auth: Any = None) -> None: ...
        def get_policy_profile(self) -> str: ...
        def _display_script_result(self, label: Any, result: Any) -> None: ...
        def _apply_script_vars(self, result: Any) -> None: ...
        def _defer_task(self, fn: Any, *args: Any, **kwargs: Any) -> None: ...

    def _as_qwidget(self) -> QWidget:
        return cast(QWidget, cast(object, self))

    def _handle_response(self, result: object, worker: Any) -> None:
        """Route a worker result to the success or error path."""
        if self._worker is not None and worker is not self._worker:
            return
        self._worker = None
        self._set_sending_state(False)
        normalized = self._normalize_exception(result)
        from equinox.core.format.error_enrichment import RichError

        if isinstance(normalized, RichError):
            self._handle_error_result(normalized, worker)
            return
        self._handle_success_result(normalized, worker)

    def _handle_error_result(self, result: Any, worker: Any) -> None:
        """Process an error result emitted by the request worker."""
        sent_request = worker.request
        logger.error(
            "Request failed: %s",
            result.message,
            extra={
                "error_type": result.exc_type,
                "url": getattr(sent_request, "url", "")[:_URL_ERROR_LOG_LIMIT],
                "method": getattr(sent_request, "method", ""),
            },
        )
        plan = build_error_handling_plan(
            error=result,
            request=sent_request,
            log_file_path=str(get_log_file()) if get_log_file() is not None else None,
            send_inherited_auth=getattr(self, "_send_inherited_auth", None),
            send_inherited_source=getattr(self, "_send_inherited_source", None),
            own_auth=self._auth,
        )
        self._status_message(plan.status_message, STATUS_DURATION_LONG)
        ErrorPresenter.request_failure(
            self._as_qwidget(),
            exc_type=result.exc_type,
            message=result.message,
            hint=result.hint,
            details=plan.copy_text,
            log_file_path=str(get_log_file()) if get_log_file() is not None else None,
        )
        notify_log_panel(self._logging_panel, "log_error", sent_request, plan.log_panel_message)
        self._apply_deferred_persistence_plan(sent_request, None, plan.deferred_plan)

    def _handle_success_result(self, result: Any, worker: Any) -> None:
        """Process a successful response emitted by the request worker."""
        response = result
        sent_request = response.request
        plan = build_success_handling_plan(
            response=response,
            send_inherited_auth=getattr(self, "_send_inherited_auth", None),
            send_inherited_source=getattr(self, "_send_inherited_source", None),
            own_auth=self._auth,
        )
        self._log_success_response(sent_request, response, plan.elapsed_ms)
        self._status_message(plan.status_message, STATUS_DURATION_LONG)
        self.response_received.emit(response)
        self._apply_captures(response)
        self._evaluate_assertions(response)
        self._run_post_script(response)
        self._add_url_to_completer(plan.completer_url)
        notify_log_panel(self._logging_panel, "log_response", sent_request, response)
        self._apply_deferred_persistence_plan(sent_request, response, plan.deferred_plan)
        self._update_auth_display(self._auth)

    def _log_success_response(self, request: Any, response: Any, elapsed_ms: int) -> None:
        """Log a successful response with structured metadata."""
        logger.info(
            "%s %s -> %d %s (%d ms)",
            request.method,
            request.url[:_URL_LOG_LIMIT],
            response.status_code,
            response.reason,
            elapsed_ms,
            extra={
                "method": request.method,
                "url": request.url[:_URL_LOG_LIMIT],
                "status": response.status_code,
                "elapsed_ms": elapsed_ms,
                "size_bytes": response.size,
            },
        )

    def _apply_captures(self, response: Any) -> None:
        """Apply capture rules and update session variables/results."""
        outcome = apply_captures(response)
        if outcome.error:
            logger.debug("Capture processing failed: %s", outcome.error)
            return
        if outcome.session_updates:
            self._session_vars.update(outcome.session_updates)
            self.session_vars_changed.emit(dict(self._session_vars))
        lines = "\n".join(outcome.display_lines) if outcome.display_lines else "ÔÇö"
        self.captures_results_label.setText(lines)

    def _run_post_script(self, response: Any) -> None:
        """Execute the post-response script when configured."""
        outcome = run_post_script(
            policy_profile=self.get_policy_profile(),
            post_script=self.post_script_editor.toPlainText(),
            response=response,
            session_vars=self._session_vars,
        )
        if outcome.skipped:
            self.post_script_result.setText(outcome.skip_message or "Skipped")
            return
        if outcome.error:
            logger.debug("Post-script failed: %s", outcome.error)
            return
        if outcome.script_result is None:
            return
        self._display_script_result(self.post_script_result, outcome.script_result)
        self._apply_script_vars(outcome.script_result)

    def _apply_deferred_persistence_plan(self, sent_request: Any, response: Any, plan: Any) -> None:
        """Execute deferred persistence side effects described by a plan."""
        if plan.save_history:
            if response is None:
                self._defer_task(
                    self._request_history.save_history_safe,
                    sent_request,
                    error=plan.history_error,
                )
            else:
                self._defer_task(self._request_history.save_history_safe, sent_request, response)
        self._persist_inherited_auth_tokens(plan.persist_inherited_token)
        self._persist_own_oauth2_token(plan.persist_own_oauth2_token)

    def _persist_inherited_auth_tokens(self, should_persist: bool = True) -> None:
        """Persist refreshed inherited OAuth2 tokens back to their owning source."""
        if not should_persist:
            return
        auth = getattr(self, "_send_inherited_auth", None)
        source = getattr(self, "_send_inherited_source", None)
        try:
            persisted = self._request_persistence.persist_inherited_oauth2_token(
                self.current_request,
                source,
                auth,
            )
        except Exception as exc:
            logger.debug("Failed to persist inherited auth tokens: %s", exc)
            return
        if not persisted:
            return
        self._inherited_auth = auth
        self._inherited_auth_source = source
        self._update_auth_display(self._auth)

    def _persist_own_oauth2_token(self, should_persist: bool = True) -> None:
        """Persist refreshed request-owned OAuth2 tokens to the request row."""
        if not should_persist:
            return
        try:
            persisted = self._request_persistence.persist_request_oauth2_token(
                self.current_request,
                self._auth,
            )
        except Exception as exc:
            logger.debug("Failed to persist own OAuth2 token: %s", exc)
            return
        if persisted:
            logger.debug("Persisted own-auth OAuth2 token for request %s", getattr(self.current_request, "id", None))
