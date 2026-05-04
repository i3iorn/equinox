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

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QMessageBox

from equinox.auth import OAuth2Auth
from equinox.core.captures import CaptureEngine
from equinox.core.error_enrichment import RichError, enrich_exception
from equinox.core.interpolation import (
    VariableInterpolator,
    collect_interpolation_variables_detailed,
)
from equinox.core.log_setup import get_log_file
from equinox.core.request import Request, Response
from equinox.core.scripts import ScriptRunner
from equinox.gui.request_panel.builder import assemble_body, inject_content_type, interpolate_auth
from equinox.gui.workers import RequestWorker

from equinox.gui.request_panel._constants import (
    HTTP_SCHEME_RE,
    PREFLIGHT_SEPARATOR,
    STATUS_DURATION_LONG,
    STATUS_DURATION_SHORT,
    WORKER_WAIT_MS,
)
from equinox.gui.request_panel.mixins._helpers import (
    notify_log_panel,
    save_history_safe,
    write_auth_to_source,
)

logger = logging.getLogger(__name__)

# ── Status message templates ──────────────────────────────────────────────────
_MSG_MISSING_URL = "Please enter a request URL."
_MSG_ERROR_PREFIX = "Error: "
_MSG_CANCELLED = "Request cancelled"
_MSG_VAR_FAILED = "Failed to expand variables"
_MSG_AUTH_VAR_FAILED = "Failed to expand variables in auth fields"

# ── String truncation for safe logging ────────────────────────────────────────
_URL_LOG_LIMIT = 80
_URL_ERROR_LOG_LIMIT = 80
_UNRESOLVED_VAR_RE = re.compile(r"\{\{([a-zA-Z0-9_-]+)\}\}")


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

    # ── Preflight validation ──────────────────────────────────────────────────

    def _run_preflight_checks(self) -> List[str]:
        """Return advisory warnings (empty list = all clear).

        Checks:
        - URL has http(s):// scheme (unless it contains {{VAR}})
        - Auth strategy accepts current configuration

        Returns:
            List of warning strings (never raises)
        """
        warnings: List[str] = []
        url = self.url_input.text().strip()

        if url and "{{" not in url and not HTTP_SCHEME_RE.match(url):
            warnings.append("URL does not start with http:// or https://")

        auth = self._auth or self._inherited_auth
        if auth is not None and hasattr(auth, "get_preflight_warning"):
            warning = auth.get_preflight_warning()
            if warning:
                warnings.append(warning)

        return warnings

    # ── Request assembly & interpolation ──────────────────────────────────────

    def _build_auth_probe(self) -> Optional[Request]:
        """Build lightweight Request for auth-hierarchy resolution.

        Returns None if no collection context available.
        """
        req = self.current_request
        if not req or not getattr(req, "collection_id", None):
            return None
        return Request(
            method="GET", url="",
            collection_id=req.collection_id,
            folder=getattr(req, "folder", None),
        )

    def _resolve_send_auth(self) -> Tuple[Any, Optional[str]]:
        """Resolve effective auth: own → DB-inherited → cached inherited.

        Returns (effective_auth, inherited_source).
        """
        if self._auth is not None:
            return self._auth, None

        effective_auth = None
        inherited_source: Optional[str] = None
        probe = self._build_auth_probe()
        if probe is not None:
            try:
                inh, inherited_source = self._collection_mgr.resolve_effective_auth(probe)
                if inh is not None:
                    effective_auth = inh
            except Exception as exc:
                logger.debug("Send-time inherited auth resolution failed: %s", exc)

        if effective_auth is None and getattr(self, "_inherited_auth", None):
            effective_auth = self._inherited_auth
            inherited_source = getattr(self, "_inherited_auth_source", None)

        return effective_auth, inherited_source

    def _run_pre_script(
        self,
        method: str,
        url: str,
        headers: Dict[str, str],
        params: Dict[str, str],
        body: Optional[str],
        variables: Dict[str, str],
    ) -> Dict[str, str]:
        """Execute pre-request script; return (possibly updated) variables.

        Script execution is isolated: exceptions are logged, not raised.
        Session variables are merged into the return dict.
        """
        pre_src = self.pre_script_editor.toPlainText()
        if not pre_src.strip():
            return variables
        try:
            req_dict = {
                "method": method, "url": url,
                "headers": dict(headers), "params": dict(params), "body": body,
            }
            result = ScriptRunner.run_pre(pre_src, req_dict, self._session_vars)
            self._display_script_result(self.pre_script_result, result)
            self._apply_script_vars(result)
            if not result.error:
                variables.update(self._session_vars)
        except Exception as exc:
            logger.debug("Pre-script failed: %s", exc)
        return variables

    @staticmethod
    def _resolve_path_params(
        path_params: Dict[str, str], variables: Dict[str, str]
    ) -> Dict[str, str]:
        """Resolve path params against global vars and other path params.

        Supports chained references such as:
        ``item = {{id}}`` and ``id = {{USER_ID}}``.
        """
        resolved: Dict[str, str] = {}
        # Resolve keys first so unusual key templates are stable before merging.
        for k, v in path_params.items():
            key = VariableInterpolator.interpolate(k, variables)
            resolved[key] = v

        # Path params can reference each other, but a key must not shadow itself.
        # Example: BASE_URL={{BASE_URL}} should resolve from global variables.
        resolved_values: Dict[str, str] = {}
        for k, v in resolved.items():
            context = dict(variables)
            context.update(resolved)
            if k in variables:
                context[k] = variables[k]
            resolved_values[k] = VariableInterpolator.interpolate(v, context)

        return resolved_values

    @staticmethod
    def _interpolate_request_fields(
        url: str,
        headers: Dict[str, str],
        params: Dict[str, str],
        body: Optional[str],
        path_params: Dict[str, str],
        variables: Dict[str, str],
    ) -> Tuple[str, Dict[str, str], Dict[str, str], Optional[str], Dict[str, str]]:
        """Interpolate {{VAR}} placeholders in all request fields.

        Returns (url, headers, params, body, path_params) with variables expanded.
        Raises on interpolation failure (caller handles & shows error dialog).
        """
        logger.debug("Interpolating variables in request (url_len=%d)", len(url))
        path_params = _RequestSendMixin._resolve_path_params(path_params, variables)

        # Allow URL/headers/query/body to reference resolved path parameters.
        merged_vars = dict(variables)
        merged_vars.update(path_params)

        url = VariableInterpolator.interpolate(url, merged_vars)
        headers = {
            VariableInterpolator.interpolate(k, merged_vars):
            VariableInterpolator.interpolate(v, merged_vars)
            for k, v in headers.items()
        }
        params = {
            VariableInterpolator.interpolate(k, merged_vars):
            VariableInterpolator.interpolate(v, merged_vars)
            for k, v in params.items()
        }
        if body:
            body = VariableInterpolator.interpolate(body, merged_vars)
        logger.debug("Variable interpolation completed successfully")
        return url, headers, params, body, path_params

    @staticmethod
    def _collect_unresolved_placeholders(
        url: str,
        headers: Dict[str, str],
        params: Dict[str, str],
        body: Optional[str],
        path_params: Dict[str, str],
    ) -> List[str]:
        """Return unresolved placeholder names across all request fields."""
        unresolved = set(_UNRESOLVED_VAR_RE.findall(url or ""))
        for k, v in headers.items():
            unresolved.update(_UNRESOLVED_VAR_RE.findall(k or ""))
            unresolved.update(_UNRESOLVED_VAR_RE.findall(v or ""))
        for k, v in params.items():
            unresolved.update(_UNRESOLVED_VAR_RE.findall(k or ""))
            unresolved.update(_UNRESOLVED_VAR_RE.findall(v or ""))
        if body:
            unresolved.update(_UNRESOLVED_VAR_RE.findall(body))
        for k, v in path_params.items():
            unresolved.update(_UNRESOLVED_VAR_RE.findall(k or ""))
            unresolved.update(_UNRESOLVED_VAR_RE.findall(v or ""))
        return sorted(unresolved)

    @staticmethod
    def _resolve_proxy_url() -> Optional[str]:
        """Read proxy settings from QSettings on the main thread.

        QSettings must NOT be accessed from background QThreads (UB on Windows).
        Returns None if proxy is not configured.
        """
        from PyQt6.QtCore import QSettings as _QSettings
        settings = _QSettings("Equinox", "Equinox")
        host = (settings.value("proxy/host") or "").strip()
        port = int(settings.value("proxy/port") or 0)
        return f"http://{host}:{port}" if (host and port > 0) else None

    @staticmethod
    def _display_script_result(label, result) -> None:
        """Update script-result label with success/error styling."""
        label.setObjectName(
            "script-result-error" if result.error else "script-result-ok"
        )
        if result.error:
            label.setText(f"Error: {result.error}")
        else:
            count = len(result.output_vars)
            label.setText(f"OK — {count} var(s) set" if count else "OK")

    def _apply_script_vars(self, result) -> None:
        """Merge script output variables into session state.

        No-op if the script produced an error or zero output vars.
        """
        if result.error or not result.output_vars:
            return
        self._session_vars.update(result.output_vars)
        self.session_vars_changed.emit(dict(self._session_vars))

    # ── Core send pipeline ────────────────────────────────────────────────────

    def _send_request(self) -> None:
        """Orchestrate full request send cycle.

        Pipeline:
        1. Validate URL
        2. Run preflight checks
        3. Gather fields from UI
        4. Resolve variables and run pre-script
        5. Interpolate fields
        6. Resolve and interpolate auth
        7. Build Request object
        8. Dispatch worker thread
        """
        url = self.url_input.text().strip()
        if not url:
            QMessageBox.warning(self, "Missing URL", _MSG_MISSING_URL)
            return

        logger.debug("_send_request() initiated: url=%s", url[:80])

        self._display_preflight_warnings()

        if self._worker is not None and self._worker.isRunning():
            return

        # ── Gather, interpolate, and validate ──
        try:
            method, headers, params, params_list, body, body_type, multipart_data, path_params = (
                self._gather_request_fields()
            )
            headers = inject_content_type(body, body_type, headers)

            variables, variable_sources = collect_interpolation_variables_detailed(
                self.db,
                collection_id=getattr(self.current_request, "collection_id", None),
                session_vars=self._session_vars,
            )
            variables = self._run_pre_script(method, url, headers, params, body, variables)

            url, headers, params, body, path_params = self._interpolate_request_fields(
                url, headers, params, body, path_params, variables,
            )

            if path_params:
                from equinox.core.urls import expand_placeholders
                url = expand_placeholders(url, path_params)
                logger.debug("URL expanded with path_params: %s", url[:100])

            unresolved = self._collect_unresolved_placeholders(
                url, headers, params, body, path_params
            )
            if unresolved:
                unresolved_details = []
                for name in unresolved:
                    value = variables.get(name)
                    unresolved_details.append(
                        f"{name}(source={variable_sources.get(name, 'missing')}, "
                        f"value_type={type(value).__name__ if value is not None else 'missing'}, "
                        f"value_is_template={bool(isinstance(value, str) and VariableInterpolator.has_variables(value))})"
                    )
                logger.warning(
                    "Unresolved placeholders before dispatch: %s (available_keys=%s)",
                    unresolved_details,
                    sorted(str(k) for k in variables.keys()),
                )
                QMessageBox.warning(
                    self,
                    "Variable Error",
                    "Failed to expand variables:\n"
                    f"Unresolved placeholders: {', '.join(unresolved_details)}",
                )
                return

        except Exception as exc:
            logger.warning("Variable interpolation failed: %s", exc)
            QMessageBox.warning(self, "Variable Error", f"{_MSG_VAR_FAILED}:\n{exc}")
            return

        # ── Resolve auth ──
        effective_auth, inherited_source = self._resolve_send_auth()

        try:
            effective_auth = interpolate_auth(
                effective_auth,
                lambda s: VariableInterpolator.interpolate(s, variables),
            )
        except Exception as exc:
            logger.warning("Auth variable interpolation failed: %s", exc)
            QMessageBox.warning(self, "Variable Error", f"{_MSG_AUTH_VAR_FAILED}:\n{exc}")
            return

        self._track_inherited_auth_for_send(effective_auth, inherited_source)

        # ── Build and dispatch ──
        request = self._build_request_object(
            method, url, headers, params, params_list, body,
            effective_auth, multipart_data, path_params,
        )
        request.headers["X-Request-ID"] = request.headers.get("X-Request-ID", str(uuid4()))
        self.current_request = request

        logger.info(
            "Sending %s %s", method, url,
            extra={"method": method, "url": url},
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

    def _gather_request_fields(
        self,
    ) -> Tuple[str, Dict[str, str], Dict[str, str], list, Optional[str], str, Optional[list], Dict[str, str]]:
        """Read all request fields from UI widgets.

        Returns:
            (method, headers, params, params_list, body, body_type, multipart_data, path_params)
        """
        method = self.method_combo.currentText()
        headers = self.headers_table.get_data()
        params = self.params_table.get_enabled_data()
        params_list = self.params_table.get_all_rows()
        path_params = self.path_params_table.get_all_data()
        body_type = self.body_type_combo.currentText()
        body, multipart_data = assemble_body(
            body_type,
            self.body_text.toPlainText().strip(),
            self._gql_query.toPlainText().strip(),
            self._gql_vars.toPlainText().strip(),
            self._get_multipart_data(),
        )
        return method, headers, params, params_list, body, body_type, multipart_data, path_params

    def _track_inherited_auth_for_send(
        self, effective_auth: Any, inherited_source: Optional[str]
    ) -> None:
        """Store inherited auth context for post-send token persistence."""
        is_inherited = self._auth is None
        self._send_inherited_auth = effective_auth if is_inherited else None
        self._send_inherited_source = inherited_source if is_inherited else None

    def _build_request_object(
        self,
        method: str,
        url: str,
        headers: Dict[str, str],
        params: Dict[str, str],
        params_list: list,
        body: Optional[str],
        effective_auth: Any,
        multipart_data: Optional[list],
        path_params: Optional[Dict[str, str]] = None,
    ) -> Request:
        """Build Request object carrying forward collection context.

        Applies send-specific overrides: interpolated fields, effective auth,
        multipart data, path parameters. Preserves collection_id, folder, id, name.
        """
        _prev = self.current_request
        return self._build_request_from_editor(
            method=method,
            url=url,
            headers=headers,
            params=params,
            params_list=params_list,
            body=body,
            auth=effective_auth,
            multipart_data=multipart_data,
            path_params=path_params or {},
            collection_id=getattr(_prev, "collection_id", None),
            folder=getattr(_prev, "folder", None),
            id=getattr(_prev, "id", None),
            name=getattr(_prev, "name", None),
        )

    def _dispatch_worker(self, request: Request) -> None:
        """Create and start the background request worker."""
        proxy = self._resolve_proxy_url()
        self._worker = RequestWorker(
            request, self,
            cookie_manager=self._cookie_manager,
            proxy=proxy,
        )
        worker_ref = self._worker
        self._worker.finished.connect(
            lambda result, w=worker_ref: self._handle_response(result, w)
        )
        self._worker.start()

    def _cancel_request(self) -> None:
        """Cancel the in-flight request and reset sending state."""
        worker = self._worker
        if worker is not None:
            worker.cancel()
            worker.quit()
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
            "Request failed: %s", result.message,
            extra={
                "error_type": result.exc_type,
                "url": getattr(_sent_request, "url", "")[:_URL_ERROR_LOG_LIMIT],
                "method": getattr(_sent_request, "method", ""),
            },
        )

        # ── UI feedback ──
        self._status_message(f"{_MSG_ERROR_PREFIX}{result.message}", STATUS_DURATION_LONG)

        # ── Error dialog ──
        from equinox.gui.widgets import CopyableMessageBox
        log_hint = f"\n\nFull details in: {get_log_file()}" if get_log_file() else ""
        dialog_text = f"{result.message}{log_hint}"
        if result.hint:
            dialog_text = f"{result.message}\n\n{result.hint}{log_hint}"
        CopyableMessageBox.critical(
            self, f"Request Failed — {result.exc_type}",
            dialog_text,
            copy_text=result.tb,
        )

        # ── Logging panel ──
        notify_log_panel(self._logging_panel, "log_error", _sent_request, result.message)

        # ── Deferred tasks ──
        self._defer_task(save_history_safe, self.db, _sent_request, error=result.message)
        self._persist_inherited_auth_tokens()

    def _handle_success_result(self, result: Response, worker: RequestWorker) -> None:
        """Process successful response from worker.

        Execute in two phases:
        1. Sync: Update UI, emit signals, run scripts/captures
        2. Deferred: DB writes and other expensive operations
        """
        response: Response = result
        elapsed_ms = int(response.elapsed * 1000)
        _sent_request = response.request

        # ── Phase 1: Immediate response handling (sync) ──

        self._log_success_response(_sent_request, response, elapsed_ms)
        self._status_message(
            f"{response.status_code} {response.reason}  —  {elapsed_ms} ms",
            STATUS_DURATION_LONG,
        )
        self.response_received.emit(response)

        # Run post-response processing pipeline (must complete before deferred tasks)
        self._apply_captures(response)
        self._evaluate_assertions(response)
        self._run_post_script(response)
        self._add_url_to_completer(getattr(response.request, "url", ""))

        notify_log_panel(self._logging_panel, "log_response", _sent_request, response)

        # ── Phase 2: Deferred tasks (async-safe on main thread) ──

        self._defer_task(save_history_safe, self.db, _sent_request, response)

        # Persist refreshed tokens (separate ownership paths)
        self._persist_inherited_auth_tokens()
        self._persist_own_oauth2_token()

        # Refresh auth display (may have been mutated by auto-refresh in worker)
        self._update_auth_display(self._auth)

    def _log_success_response(
        self, request: Request, response: Response, elapsed_ms: int
    ) -> None:
        """Log a successful response with structured context."""
        logger.info(
            "%s %s -> %d %s (%d ms)",
            request.method, request.url[:_URL_LOG_LIMIT],
            response.status_code, response.reason, elapsed_ms,
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
        try:
            caps_raw = getattr(response.request, "captures", [])
            if not caps_raw:
                return
            results = CaptureEngine.apply_all(
                CaptureEngine.from_dict_list(caps_raw), response
            )
            for r in results:
                self._session_vars[r.variable] = r.value
            self.session_vars_changed.emit(dict(self._session_vars))
            lines = [
                f"{'✓' if r.success else '✗'} {r.variable} = {r.value!r}"
                + (f"  ({r.error})" if not r.success else "")
                for r in results
            ]
            self.captures_results_label.setText("\n".join(lines) if lines else "—")
        except Exception as exc:
            logger.debug("Capture processing failed: %s", exc, exc_info=True)

    def _run_post_script(self, response: Response) -> None:
        """Execute post-response script if defined."""
        post_src = self.post_script_editor.toPlainText()
        if not post_src.strip():
            return
        try:
            resp_dict: Dict[str, Any] = {
                "status_code": response.status_code,
                "headers": dict(response.headers),
                "body": response.text if hasattr(response, "text") else "",
                "json": None,
            }
            try:
                resp_dict["json"] = response.json()
            except Exception:
                logger.debug("Response body is not JSON; post-script will see json=None")
            result = ScriptRunner.run_post(post_src, resp_dict, self._session_vars)
            self._display_script_result(self.post_script_result, result)
            self._apply_script_vars(result)
        except Exception as exc:
            logger.debug("Post-script failed: %s", exc)

    # ── Token persistence ─────────────────────────────────────────────────────

    def _persist_inherited_auth_tokens(self) -> None:
        """Save refreshed tokens on inherited auth to DB.

        After OAuth2Auth.apply() auto-refreshes, write the new token back
        to collection/folder so subsequent requests reuse it.
        """
        auth = getattr(self, "_send_inherited_auth", None)
        source = getattr(self, "_send_inherited_source", None)
        if auth is None or source is None:
            return
        if not isinstance(auth, OAuth2Auth) or not auth.access_token:
            return
        try:
            req = self.current_request
            if not req or not req.collection_id:
                return
            write_auth_to_source(self._collection_mgr, req.collection_id, source, auth)
            self._inherited_auth = auth
            self._inherited_auth_source = source
            self._update_auth_display(self._auth)
        except Exception as exc:
            logger.debug("Failed to persist inherited auth tokens: %s", exc)

    def _persist_own_oauth2_token(self) -> None:
        """Save refreshed OAuth2 token on own auth to request row.

        _send_request() never sets dirty, so "send without edits" would
        discard token on next navigation. Persist directly regardless of dirty state.
        """
        if not isinstance(self._auth, OAuth2Auth) or not self._auth.access_token:
            return
        req = self.current_request
        if not req or not getattr(req, "id", None):
            return
        try:
            self._collection_mgr.update_request_auth(req.id, self._auth)
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
