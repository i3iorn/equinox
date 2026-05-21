"""Send / response / script mixin for RequestPanel.

Contains ``_RequestSendMixin`` — all methods related to dispatching HTTP
requests, handling responses, running pre/post scripts, applying captures,
and persisting history.

This mixin has no ``__init__`` and relies on ``self.*`` attributes set by
``RequestPanel.__init__`` (PyQt6 MRO is respected).

Responsibilities:
- Request assembly and variable interpolation (send phase)
- Worker thread lifecycle management
- Response processing pipeline (success/error/post-request tasks)
- Session state updates (captures, scripts, auth tokens)
- Deferred persistence (history)
"""

# mypy: disable-error-code=attr-defined

from __future__ import annotations

import logging

from PyQt6.QtCore import QTimer

from equinox.application.requests import (
    apply_captures,
    build_error_handling_plan,
    build_preflight_issues,
    build_success_handling_plan,
    issues_to_messages,
    prepare_send,
    run_post_script,
)
from equinox.core.format.error_enrichment import RichError, enrich_exception
from equinox.core.log_setup import get_log_file
from equinox.core.request import Request, Response
from equinox.gui.error_presenter import ErrorPresenter
from equinox.gui.request_panel._constants import (
    PREFLIGHT_SEPARATOR,
    STATUS_DURATION_LONG,
    STATUS_DURATION_SHORT,
    WORKER_WAIT_MS,
)
from equinox.gui.request_panel.mixins._helpers import (
    notify_log_panel,
)
from equinox.gui.workers import RequestWorker

logger = logging.getLogger(__name__)

# ── Status message templates ──────────────────────────────────────────────────
_MSG_MISSING_URL = "Please enter a request URL."
_MSG_CANCELLED = "Request cancelled"
_MSG_VAR_FAILED = "Failed to expand variables"
_MSG_AUTH_VAR_FAILED = "Failed to expand variables in auth fields"

# ── String truncation for safe logging ────────────────────────────────────────
_URL_LOG_LIMIT = 80
_URL_ERROR_LOG_LIMIT = 80


class _RequestSendMixin:
    """Methods for sending requests, handling responses, and managing the send lifecycle.

    Pipeline stages:
    1. **Pre-flight** — Validate URL, check auth configuration
    2. **Assembly** — Gather fields from UI (method, headers, params, body, path_params)
    3. **Interpolation** — Expand {{variable}} placeholders in all fields
    4. **Auth resolution** — Resolve effective auth (own → inherited → cached)
    5. **Worker dispatch** — Create and start background worker thread
    6. **Response routing** — Route to success or error handler
    7. **Post-request** — Captures, scripts, token persistence, history, recommender

    Key invariants:
    - All user input is validated before use (zero-trust)
    - HTTP client never sees uninterpolated URLs or variables
    - Dirty flag is never set by send (only by user edits)
    - Worker results are guarded against stale responses (multithreading safety)
    - All task deferrals use QTimer to unblock the UI
    """

    _worker: RequestWorker | None

    # ── Preflight validation ──────────────────────────────────────────────────

    def _run_preflight_checks(self) -> list[str]:
        """Return advisory warnings (empty list = all clear).

        Checks:
        - URL has http(s):// scheme (unless it contains {{VAR}})
        - Auth strategy accepts current configuration

        Returns:
            List of warning strings (never raises)
        """
        issues = build_preflight_issues(
            url=self.url_input.text().strip(),
            policy_profile=self.get_policy_profile(),
            verify_ssl=self.verify_ssl_check.isChecked(),
            follow_redirects=self.follow_redirects_check.isChecked(),
            pre_script=self.pre_script_editor.toPlainText(),
            post_script=self.post_script_editor.toPlainText(),
            auth=self._auth or self._inherited_auth,  # type: ignore[has-type]
        )
        return issues_to_messages(issues)

    # ── Core send pipeline ────────────────────────────────────────────────────

    def _send_request(self) -> None:
        """Orchestrate full request send cycle.

        Pipeline (GUI responsibilities):
        1. Validate URL present
        2. Display preflight warnings
        3. Guard against strict policy blocks
        4. Call prepare_send() service
        5. Render pre-script result label
        6. Merge pre-script env_changes into session_vars
        7. Track inherited auth for post-send token persistence
        8. Dispatch worker thread

        All preparation, variable collection, auth resolution, interpolation,
        and request construction are delegated to the send service (Phase 5).

        NOTE: Worker creation stays in the GUI as an intermediate state while
        the execution adapter boundary is defined in a later phase.
        """
        snapshot = self._build_request_editor_snapshot()
        url = snapshot.url
        if not url:
            ErrorPresenter.warning(self, _MSG_MISSING_URL, title="Missing URL")
            return

        logger.debug("_send_request() initiated: url=%s", url[:80])

        self._display_preflight_warnings()

        try:
            if str(self.get_policy_profile()).lower() == "strict":
                if url.lower().startswith("http://"):
                    ErrorPresenter.warning(
                        self,
                        "Strict policy blocks insecure HTTP requests. Use https:// instead.",
                        title="Strict Policy",
                    )
                    return
                if not snapshot.verify_ssl:
                    ErrorPresenter.warning(
                        self,
                        "Strict policy requires SSL certificate verification.",
                        title="Strict Policy",
                    )
                    return
        except Exception:
            logger.debug("Failed to evaluate strict policy block checks", exc_info=True)

        if self._worker is not None and self._worker.isRunning():
            return

        # ── Delegate to send orchestration service ──
        result = prepare_send(
            snapshot=snapshot,
            db=self.db,
            collection_manager=getattr(self, "_request_persistence", None),
            own_auth=self._auth,
            inherited_auth=getattr(self, "_inherited_auth", None),
            inherited_auth_source=getattr(self, "_inherited_auth_source", None),
            policy_profile=self.get_policy_profile(),
        )

        if not result.ready:
            for issue in result.blocking_issues:
                title = {
                    "variables.unresolved": "Variable Error",
                    "interpolation.failed": "Variable Error",
                    "auth.interpolation_failed": "Variable Error",
                    "body.assembly_failed": "Request Validation",
                    "request.construction_failed": "Request Error",
                }.get(issue.code, "Request Error")
                ErrorPresenter.warning(self, issue.message, title=title)
            return

        pkg = result.package

        # ── Render pre-script result (GUI responsibility — Step 5.7) ──
        if pkg.pre_script_result is not None:
            self._display_script_result(self.pre_script_result, pkg.pre_script_result)
            self._apply_script_vars(pkg.pre_script_result)
        elif str(self.get_policy_profile()).lower() == "strict" and snapshot.pre_script.strip():
            self.pre_script_result.setText("Skipped by strict policy")

        # ── Track inherited auth for post-send token persistence ──
        self._send_inherited_auth = pkg.request.auth if pkg.is_auth_inherited else None
        self._send_inherited_source = pkg.inherited_auth_source if pkg.is_auth_inherited else None

        # ── Dispatch ──
        request = pkg.request
        self.current_request = request

        logger.info(
            "Sending %s %s",
            request.method,
            request.url,
            extra={"method": request.method, "url": request.url},
        )
        notify_log_panel(self._logging_panel, "log_request", request)

        self.request_sent.emit(request)
        self._set_sending_state(True)
        self._dispatch_worker(request)

    def _display_preflight_warnings(self) -> None:
        """Run preflight checks and show/hide the warning banner."""
        pf_warnings = self._run_preflight_checks()
        if pf_warnings:
            self._preflight_label.setText(PREFLIGHT_SEPARATOR.join(pf_warnings))
            self._preflight_banner.setVisible(True)
            logger.debug("Preflight warnings: %s", pf_warnings)
        else:
            self._preflight_banner.setVisible(False)

    @staticmethod
    def _resolve_proxy_url() -> str | None:
        """Read proxy settings from QSettings on the main thread.

        QSettings must NOT be accessed from background QThreads (UB on Windows).
        Returns None if proxy is not configured.
        """
        from equinox.gui.ui_common import resolve_proxy_url

        return resolve_proxy_url(logger=logger)

    @staticmethod
    def _display_script_result(label, result) -> None:
        """Update script-result label with success/error styling."""
        label.setObjectName("script-result-error" if result.error else "script-result-ok")
        if result.error:
            label.setText(f"Error: {result.error}")
        else:
            count = len(result.env_changes)
            label.setText(f"OK — {count} var(s) set" if count else "OK")

    def _apply_script_vars(self, result) -> None:
        """Merge script output variables into session state.

        No-op if the script produced an error or zero env changes.
        """
        if result.error or not result.env_changes:
            return
        self._session_vars.update(result.env_changes)
        self.session_vars_changed.emit(dict(self._session_vars))

    def _dispatch_worker(self, request: Request) -> None:
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
        """Cancel the in-flight request and reset sending state."""
        worker = self._worker
        if worker is not None:
            worker.cancel()
            worker.wait(WORKER_WAIT_MS)
            self._worker = None
        self._set_sending_state(False)
        self._status_message(_MSG_CANCELLED, STATUS_DURATION_SHORT)

    # ── Response handling ─────────────────────────────────────────────────────

    def _defer_task(self, fn, *args, **kwargs) -> None:
        """Defer a task to unblock the main thread.

        Schedules the given function to run on the next event loop iteration.
        Used for DB writes, recommender queries, and other expensive operations.

        Args:
            fn: Callable to execute (typically a lambda or partial)
            *args, **kwargs: Arguments to pass to fn
        """
        QTimer.singleShot(0, lambda: fn(*args, **kwargs))

    def _normalize_exception(self, result: object) -> object:
        """Normalize exceptions to RichError without recursion.

        If result is an Exception that's not already RichError, attempts to
        enrich it. Falls back to a minimal RichError if enrichment fails.

        Args:
            result: Object that might be an exception

        Returns:
            Result unchanged, or RichError if it was an un-enriched exception
        """
        if isinstance(result, Exception) and not isinstance(result, RichError):
            try:
                return enrich_exception(result)
            except Exception:
                return RichError(
                    exc_type=type(result).__name__,
                    message=str(result) or "Unknown error",
                    tb="",
                )
        return result

    def _handle_response(self, result: object, worker: RequestWorker) -> None:
        """Route worker result to success or error handler.

        Guards against stale results from cancelled/replaced workers.
        Normalizes exceptions into RichError objects.
        """
        if self._worker is not None and worker is not self._worker:
            return  # Stale result from cancelled/replaced worker
        self._worker = None
        self._set_sending_state(False)

        result = self._normalize_exception(result)

        if isinstance(result, RichError):
            self._handle_error_result(result, worker)
        else:
            self._handle_success_result(result, worker)

    def _handle_error_result(self, result: RichError, worker: RequestWorker) -> None:
        """Process error result from worker."""
        _sent_request = worker.request

        # ── Logging ──
        logger.error(
            "Request failed: %s",
            result.message,
            extra={
                "error_type": result.exc_type,
                "url": getattr(_sent_request, "url", "")[:_URL_ERROR_LOG_LIMIT],
                "method": getattr(_sent_request, "method", ""),
            },
        )

        # ── UI feedback ──
        plan = build_error_handling_plan(
            error=result,
            request=_sent_request,
            log_file_path=get_log_file(),
            send_inherited_auth=getattr(self, "_send_inherited_auth", None),
            send_inherited_source=getattr(self, "_send_inherited_source", None),
            own_auth=self._auth,
        )
        self._status_message(plan.status_message, STATUS_DURATION_LONG)

        # ── Error dialog ──
        ErrorPresenter.request_failure(
            self,
            exc_type=result.exc_type,
            message=result.message,
            hint=result.hint,
            details=plan.copy_text,
            log_file_path=get_log_file(),
        )

        # ── Logging panel ──
        notify_log_panel(self._logging_panel, "log_error", _sent_request, plan.log_panel_message)

        # ── Deferred tasks ──
        self._apply_deferred_persistence_plan(
            sent_request=_sent_request,
            response=None,
            plan=plan.deferred_plan,
        )

    def _handle_success_result(self, result: Response, worker: RequestWorker) -> None:
        """Process successful response from worker.

        Execute in two phases:
        1. Sync: Update UI, emit signals, run scripts/captures
        2. Deferred: DB writes and other expensive operations
        """
        response: Response = result
        _sent_request = response.request
        plan = build_success_handling_plan(
            response=response,
            send_inherited_auth=getattr(self, "_send_inherited_auth", None),
            send_inherited_source=getattr(self, "_send_inherited_source", None),
            own_auth=self._auth,
        )

        # ── Phase 1: Immediate response handling (sync) ──

        self._log_success_response(_sent_request, response, plan.elapsed_ms)
        self._status_message(plan.status_message, STATUS_DURATION_LONG)
        self.response_received.emit(response)

        # Run post-response processing pipeline (must complete before deferred tasks)
        self._apply_captures(response)
        self._evaluate_assertions(response)
        self._run_post_script(response)
        self._add_url_to_completer(plan.completer_url)

        notify_log_panel(self._logging_panel, "log_response", _sent_request, response)

        # ── Phase 2: Deferred tasks (async-safe on main thread) ──

        self._apply_deferred_persistence_plan(
            sent_request=_sent_request,
            response=response,
            plan=plan.deferred_plan,
        )

        # Refresh auth display (may have been mutated by auto-refresh in worker)
        self._update_auth_display(self._auth)

    def _log_success_response(self, request: Request, response: Response, elapsed_ms: int) -> None:
        """Log a successful response with structured context."""
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

    # ── Post-response processing pipeline ──────────────────────────────────────

    def _apply_captures(self, response: Response) -> None:
        """Run capture rules against response; update session vars."""
        outcome = apply_captures(response)
        if outcome.error:
            logger.debug("Capture processing failed: %s", outcome.error)
            return
        if outcome.session_updates:
            self._session_vars.update(outcome.session_updates)
            self.session_vars_changed.emit(dict(self._session_vars))
        self.captures_results_label.setText(
            "\n".join(outcome.display_lines) if outcome.display_lines else "—"
        )

    def _run_post_script(self, response: Response) -> None:
        """Execute post-response script if defined."""
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

    def _apply_deferred_persistence_plan(
        self, sent_request: Request, response: Response | None, plan
    ) -> None:
        """Execute deferred persistence side effects from a service-level plan."""
        if plan.save_history:
            if response is None:
                self._defer_task(
                    self._request_history.save_history_safe, sent_request, error=plan.history_error
                )
            else:
                self._defer_task(self._request_history.save_history_safe, sent_request, response)
        self._persist_inherited_auth_tokens(should_persist=plan.persist_inherited_token)
        self._persist_own_oauth2_token(should_persist=plan.persist_own_oauth2_token)

    # ── Token persistence ─────────────────────────────────────────────────────

    def _persist_inherited_auth_tokens(self, should_persist: bool = True) -> None:
        """Save refreshed tokens on inherited auth to DB.

        After OAuth2Auth.apply() auto-refreshes, write the new token back
        to collection/folder so subsequent requests reuse it.
        """
        if not should_persist:
            return
        auth = getattr(self, "_send_inherited_auth", None)
        source = getattr(self, "_send_inherited_source", None)
        try:
            req = self.current_request
            persisted = self._request_persistence.persist_inherited_oauth2_token(req, source, auth)
            if not persisted:
                return
            self._inherited_auth = auth
            self._inherited_auth_source = source
            self._update_auth_display(self._auth)
        except Exception as exc:
            logger.debug("Failed to persist inherited auth tokens: %s", exc)

    def _persist_own_oauth2_token(self, should_persist: bool = True) -> None:
        """Save refreshed OAuth2 token on own auth to request row.

        _send_request() never sets dirty, so "send without edits" would
        discard token on next navigation. Persist directly regardless of dirty state.
        """
        if not should_persist:
            return
        req = self.current_request
        try:
            persisted = self._request_persistence.persist_request_oauth2_token(req, self._auth)
            if not persisted:
                return
            logger.debug("Persisted own-auth OAuth2 token for request %d", req.id)
        except Exception as exc:
            logger.debug("Failed to persist own OAuth2 token: %s", exc)

    # ── UI state management ───────────────────────────────────────────────────

    def _set_sending_state(self, sending: bool) -> None:
        """Toggle UI between idle and sending states."""
        enabled = not sending
        self.send_button.setEnabled(enabled)
        self.url_input.setEnabled(enabled)
        self.method_combo.setEnabled(enabled)
        self.cancel_button.setVisible(sending)
        if sending:
            self._elapsed_secs = 0.0
            self._elapsed_timer.start()
            self.send_button.setText("0.0s…")
        else:
            self._elapsed_timer.stop()
            self.send_button.setText("Send")

    def _tick_elapsed(self) -> None:
        """Update elapsed time display."""
        self._elapsed_secs += 0.1
        self.send_button.setText(f"{self._elapsed_secs:.1f}s…")
