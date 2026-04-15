"""Send / response / script mixin for RequestPanel.

Contains ``_RequestSendMixin`` — all methods related to dispatching HTTP
requests, handling responses, running pre/post scripts, applying captures,
persisting history, and publishing recommender hints.

This mixin has no ``__init__`` and relies on ``self.*`` attributes set by
``RequestPanel.__init__`` (PyQt6 MRO is respected).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QMessageBox

from equinox.auth import OAuth2Auth
from equinox.core.captures import CaptureEngine
from equinox.core.error_enrichment import RichError, enrich_exception
from equinox.core.interpolation import VariableInterpolator, collect_interpolation_variables
from equinox.core.log_setup import get_log_file
from equinox.core.request import Request, Response
from equinox.core.scripts import ScriptRunner
from equinox.gui.request_panel.builder import assemble_body, inject_content_type, interpolate_auth
from equinox.gui.theme import Colors
from equinox.gui.workers import RequestWorker

from equinox.gui.request_panel._constants import (
    HTTP_SCHEME_RE,
    PREFLIGHT_SEPARATOR,
    RECOMMENDER_HIGH_CONFIDENCE,
    RECOMMENDER_TOP_N,
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


class _RequestSendMixin:
    """Methods for sending requests, handling responses, and managing the send lifecycle.

    Responsible for:
    - Pre-flight validation and advisory warnings
    - Request field assembly and variable interpolation
    - Auth resolution and interpolation
    - Worker thread management (start, cancel, stale-result guard)
    - Response processing (success/error dispatch)
    - Pre/post scripts and captures
    - History persistence (deferred via QTimer)
    - Recommender suggestions for failed requests
    """

    # ── Preflight ─────────────────────────────────────────────────────

    def _run_preflight_checks(self) -> List[str]:
        """Return a list of advisory warning strings (empty = all clear).

        Checks:
        - URL has http(s):// scheme (unless it contains ``{{VAR}}``)
        - Auth has required fields configured (via strategy's get_preflight_warning)

        Returns:
            List of warning strings (empty if no issues)
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

    # ── Extracted helpers for _send_request ────────────────────────────

    def _build_auth_probe(self) -> Optional[Request]:
        """Build a lightweight Request for auth-hierarchy resolution.

        Returns ``None`` if no collection context is available.
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
        """Resolve the effective auth for the current send.

        Resolution order: own auth → DB-resolved inherited → cached inherited.
        Returns ``(effective_auth, inherited_source)``.
        """
        if self._auth is not None:
            return self._auth, None

        # Re-resolve from DB at send time so tokens are always fresh.
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

        # Fallback to cached inherited auth if DB resolution failed
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
        """Execute the pre-request script and return the (possibly updated) variables."""
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
    def _interpolate_request_fields(
        url: str,
        headers: Dict[str, str],
        params: Dict[str, str],
        body: Optional[str],
        path_params: Dict[str, str],
        variables: Dict[str, str],
    ) -> Tuple[str, Dict[str, str], Dict[str, str], Optional[str], Dict[str, str]]:
        """Interpolate ``{{VAR}}`` placeholders in all request fields.

        Returns ``(url, headers, params, body, path_params)`` with variables expanded.
        Raises on interpolation failure so the caller can show a user-facing error.
        """
        logger.debug("Interpolating variables in request (url_len=%d)", len(url))
        url = VariableInterpolator.interpolate(url, variables)
        headers = {
            VariableInterpolator.interpolate(k, variables):
            VariableInterpolator.interpolate(v, variables)
            for k, v in headers.items()
        }
        params = {
            VariableInterpolator.interpolate(k, variables):
            VariableInterpolator.interpolate(v, variables)
            for k, v in params.items()
        }
        path_params = {
            VariableInterpolator.interpolate(k, variables):
            VariableInterpolator.interpolate(v, variables)
            for k, v in path_params.items()
        }
        if body:
            body = VariableInterpolator.interpolate(body, variables)
        logger.debug("Variable interpolation completed successfully")
        return url, headers, params, body, path_params

    @staticmethod
    def _resolve_proxy_url() -> Optional[str]:
        """Read proxy settings from QSettings on the main thread.

        QSettings must NOT be accessed from a background QThread (UB on
        Windows with the native registry backend).
        """
        from PyQt6.QtCore import QSettings as _QSettings
        settings = _QSettings("Equinox", "Equinox")
        host = (settings.value("proxy/host") or "").strip()
        port = int(settings.value("proxy/port") or 0)
        return f"http://{host}:{port}" if (host and port > 0) else None

    @staticmethod
    def _display_script_result(label, result) -> None:
        """Update a script-result QLabel with success/error styling."""
        if result.error:
            label.setText(f"Error: {result.error}")
            label.setStyleSheet(f"color: {Colors.RED};")
        else:
            count = len(result.output_vars)
            label.setText(f"OK — {count} var(s) set" if count else "OK")
            label.setStyleSheet(f"color: {Colors.GREEN};")

    def _apply_script_vars(self, result) -> None:
        """Merge script output variables into session state and emit change signal.

        No-op when the script produced an error or zero output vars.
        """
        if result.error or not result.output_vars:
            return
        self._session_vars.update(result.output_vars)
        self.session_vars_changed.emit(dict(self._session_vars))

    # ── Core send ─────────────────────────────────────────────────────

    def _send_request(self) -> None:
        """Orchestrate a full request send cycle.

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
            QMessageBox.warning(self, "Missing URL", "Please enter a request URL.")
            return

        logger.debug("_send_request() initiated: url=%s", url[:80])

        # Pre-flight advisory warnings (non-blocking)
        self._display_preflight_warnings()

        # Don't start a second request while one is in flight
        if self._worker is not None and self._worker.isRunning():
            return

        # ── Gather fields from UI ─────────────────────────────────────
        method, headers, params, params_list, body, body_type, multipart_data, path_params = (
            self._gather_request_fields()
        )
        headers = inject_content_type(body, body_type, headers)

        # ── Variable interpolation ────────────────────────────────────
        variables = collect_interpolation_variables(
            self.db,
            collection_id=getattr(self.current_request, "collection_id", None),
            session_vars=self._session_vars,
        )

        # Pre-request script (may update variables in-place)
        variables = self._run_pre_script(method, url, headers, params, body, variables)

        try:
            url, headers, params, body, path_params = self._interpolate_request_fields(
                url, headers, params, body, path_params, variables,
            )
            # After interpolating path_params, apply them to the URL by using them as variables
            # This replaces {{param_name}} placeholders in the URL with their interpolated values
            if path_params:
                from equinox.core.urls import expand_placeholders
                url = expand_placeholders(url, path_params)
                logger.debug("URL expanded with path_params: %s", url[:100])
        except Exception as exc:
            logger.warning("Variable interpolation failed: %s", exc)
            QMessageBox.warning(
                self, "Variable Error", f"Failed to expand variables:\n{exc}",
            )
            return

        # ── Auth resolution ───────────────────────────────────────────
        effective_auth, inherited_source = self._resolve_send_auth()

        try:
            effective_auth = interpolate_auth(
                effective_auth,
                lambda s: VariableInterpolator.interpolate(s, variables),
            )
        except Exception as exc:
            logger.warning("Auth variable interpolation failed: %s", exc)
            QMessageBox.warning(
                self, "Variable Error",
                f"Failed to expand variables in auth fields:\n{exc}",
            )
            return

        # Track for post-send save-back of refreshed tokens
        self._track_inherited_auth_for_send(effective_auth, inherited_source)

        # ── Build and dispatch ────────────────────────────────────────
        request = self._build_request_object(
            method, url, headers, params, params_list, body,
            effective_auth, multipart_data, path_params,
        )
        self.current_request = request

        logger.info(
            "Sending %s %s", method, url,
            extra={"method": method, "url": url},
        )
        notify_log_panel(self._logging_panel, "log_request", request)

        self.request_sent.emit(request)
        self._set_sending_state(True)
        # NOTE: Do NOT call _clear_dirty() here.  Sending is not a save —
        # the user's edits must still be autosaved to the DB when navigating
        # away.  Clearing the flag here would silently discard changes.

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
        params = self.params_table.get_enabled_data()     # only checked rows are sent
        params_list = self.params_table.get_all_rows()     # full list incl. disabled
        path_params = self.path_params_table.get_all_data()  # all path parameters from table
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
        """Store inherited auth context for post-send token persistence.

        Args:
            effective_auth: Resolved auth strategy
            inherited_source: Source identifier or None if own auth
        """
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
        """Build the Request object carrying forward collection context.

        Delegates to ``_build_request_from_editor`` (defined on RequestPanel)
        for field extraction, then applies the send-specific overrides:
        interpolated URL/headers/params/body, effective auth, multipart
        data, and path parameters.  Preserves collection_id, folder, id, and name from the
        currently loaded request.

        Returns:
            Fully constructed Request object
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
        """Create and start the background request worker.

        Args:
            request: Fully assembled Request to send
        """
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
        self._status_message("Request cancelled", STATUS_DURATION_SHORT)

    # ── Response handling ─────────────────────────────────────────────

    def _handle_response(self, result: object, worker: RequestWorker) -> None:
        """Route worker result to success or error handler.

        Guards against stale results from cancelled/replaced workers.
        Normalizes un-enriched exceptions into RichError objects.

        Args:
            result: Response object or Exception from worker
            worker: Worker that produced the result
        """
        # Stale result guard: only applies when _worker is still set
        if self._worker is not None and worker is not self._worker:
            return  # Stale result from a cancelled/replaced worker
        self._worker = None
        self._set_sending_state(False)

        # Normalise un-enriched exceptions without recursion.
        if isinstance(result, Exception) and not isinstance(result, RichError):
            try:
                result = enrich_exception(result)
            except Exception:
                result = RichError(
                    exc_type=type(result).__name__,
                    message=str(result) or "Unknown error",
                    tb="",
                )

        if isinstance(result, RichError):
            self._handle_error_result(result, worker)
        else:
            self._handle_success_result(result, worker)

    def _handle_error_result(self, result: RichError, worker: RequestWorker) -> None:
        """Process an error result from a completed request worker."""
        # Use worker.request — the request the worker actually processed.
        # self.current_request may have been replaced if the user navigated
        # while the worker was in-flight.
        _sent_request = worker.request
        logger.error(
            "Request failed: %s", result.message,
            extra={
                "error_type": result.exc_type,
                "url": getattr(_sent_request, "url", ""),
                "method": getattr(_sent_request, "method", ""),
            },
        )
        self._status_message(f"Error: {result.message}", STATUS_DURATION_LONG)

        # Rich dialog: show type + message + hint about log file
        from equinox.gui.widgets import CopyableMessageBox
        log_hint = f"\n\nFull details in: {get_log_file()}" if get_log_file() else ""
        CopyableMessageBox.critical(
            self, f"Request Failed — {result.exc_type}",
            f"{result.message}{log_hint}",
            copy_text=result.tb,
        )

        notify_log_panel(self._logging_panel, "log_error", _sent_request, result.message)

        # Defer DB write and recommender so the UI updates first.
        _err_msg = result.message
        QTimer.singleShot(0, lambda: save_history_safe(self.db, _sent_request, error=_err_msg))
        self._persist_inherited_auth_tokens()
        QTimer.singleShot(0, lambda: self._publish_recommender_hints(_sent_request))

    def _handle_success_result(self, result: Response, worker: RequestWorker) -> None:
        """Process a successful response from a completed request worker."""
        response: Response = result
        elapsed_ms = int(response.elapsed * 1000)
        # response.request is the actual request the worker sent —
        # use it in preference to self.current_request which may have
        # been replaced while the worker was in-flight.
        _sent_request = response.request
        logger.info(
            "%s %s -> %d %s (%d ms)",
            _sent_request.method, _sent_request.url,
            response.status_code, response.reason, elapsed_ms,
            extra={
                "method": _sent_request.method,
                "url": _sent_request.url,
                "status": response.status_code,
                "elapsed_ms": elapsed_ms,
                "size_bytes": response.size,
            },
        )
        self._status_message(
            f"{response.status_code} {response.reason}  —  {elapsed_ms} ms",
            STATUS_DURATION_LONG,
        )
        self.response_received.emit(response)
        self._apply_captures(response)
        self._evaluate_assertions(response)
        self._run_post_script(response)
        self._refresh_url_completer()

        notify_log_panel(self._logging_panel, "log_response", _sent_request, response)

        # Defer DB write so the UI renders the response instantly.
        QTimer.singleShot(0, lambda: save_history_safe(self.db, _sent_request, response))

        # If response indicates failure (HTTP 4xx/5xx), offer recommender hints
        if response.status_code >= 400:
            QTimer.singleShot(0, lambda: self._publish_recommender_hints(_sent_request))

        # Save refreshed tokens back to DB so subsequent requests (and
        # navigation) reuse the cached token rather than fetching a new one.
        #
        # Two separate paths:
        #  • Inherited auth (self._auth is None): token lives on the
        #    collection/folder row — handled by _persist_inherited_auth_tokens.
        #  • Own auth (self._auth is OAuth2Auth): token lives on the request
        #    row — handled by _persist_own_oauth2_token.  autosave_current()
        #    would also do this, but only when dirty; _send_request()
        #    deliberately never sets the dirty flag, so a "send without edits"
        #    would lose the token on the next navigation without this call.
        self._persist_inherited_auth_tokens()
        self._persist_own_oauth2_token()

        # Refresh the auth display — OAuth2Auth.apply() may have
        # auto-refreshed the token in the worker thread, mutating
        # self._auth in-place.  Without this, the Auth tab preview
        # would still show the pre-send token.
        self._update_auth_display(self._auth)

    # ── Captures ──────────────────────────────────────────────────────

    def _apply_captures(self, response: Response) -> None:
        """Run capture rules against the response and update session vars."""
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

    # ── Scripts ───────────────────────────────────────────────────────

    def _run_post_script(self, response: Response) -> None:
        """Execute the post-response script if one is defined."""
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

    # ── Token persistence ─────────────────────────────────────────────

    def _persist_inherited_auth_tokens(self) -> None:
        """Save back refreshed tokens on inherited auth to the DB.

        After ``OAuth2Auth.apply()`` auto-refreshes a token, the in-memory
        object has the new ``access_token`` and ``expires_at``.  This method
        writes them back to the collection or folder so subsequent requests
        reuse the token instead of fetching a new one every time.
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
            # Update display to show the fresh token info
            self._inherited_auth = auth
            self._inherited_auth_source = source
            self._update_auth_display(self._auth)
        except Exception as exc:
            logger.debug("Failed to persist inherited auth tokens: %s", exc)

    def _persist_own_oauth2_token(self) -> None:
        """Save a freshly-fetched OAuth2 access token when the request has its own auth.

        ``_send_request()`` deliberately never sets the dirty flag, so a
        "send without any edits" scenario would silently discard the fetched
        token on the next navigation.  This persists it directly via
        ``update_request_auth`` regardless of dirty state.
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

    # ── Recommender ───────────────────────────────────────────────────

    def _publish_recommender_hints(self, request: Request) -> None:
        """Generate suggestions for *request* from local history and publish them.

        Runs the :class:`~equinox.intelligence.Recommender`, converts results to
        Intelligence :class:`~equinox.core.response_intelligence.models.Finding`
        objects, and pushes them to the Intelligence panel.  All exceptions are
        swallowed so this never interrupts the normal send/error flow.
        """
        try:
            from equinox.intelligence import Recommender
            from equinox.core.response_intelligence.models import (
                Category, Finding, Severity,
            )
        except Exception:
            logger.debug("Recommender or intelligence models unavailable", exc_info=True)
            return

        try:
            suggestions = Recommender(self.db).generate_suggestions(
                {"method": getattr(request, "method", ""),
                 "url": getattr(request, "url", "")},
                top_n=RECOMMENDER_TOP_N,
            )
            if not suggestions:
                return

            findings = self._suggestions_to_findings(
                suggestions, Category, Finding, Severity,
            )

            win = self.window()
            rp = getattr(win, "response_panel", None)
            if rp and hasattr(rp, "intelligence_panel"):
                rp.intelligence_panel.display_findings(findings)
                rp.set_intelligence_badge(len(findings))
            else:
                self._status_message(
                    "Suggestions available (open Intelligence panel)",
                    STATUS_DURATION_LONG,
                )
        except Exception:
            logger.debug("Recommender failed", exc_info=True)

    @staticmethod
    def _suggestions_to_findings(
        suggestions: List[Dict[str, Any]],
        Category: Any,
        Finding: Any,
        Severity: Any,
    ) -> list:
        """Convert raw recommender suggestions to Finding objects."""
        findings = []
        for s in suggestions:
            stype = s.get("type")
            if stype == "header":
                title = f"Suggested header: {s.get('key')}"
                desc = (
                    f"Set header {s.get('key')} = {s.get('suggested_value')} "
                    f"(confidence {s.get('confidence'):.2f})"
                )
            elif stype == "query":
                title = f"Suggested query parameter: {s.get('key')}"
                desc = (
                    f"Add query param {s.get('key')} "
                    f"(seen in {s.get('based_on')} requests, "
                    f"confidence {s.get('confidence'):.2f})"
                )
            else:
                title = "Suggested change"
                desc = str(s)
            severity = (
                Severity.WARNING if s.get("confidence", 0) >= RECOMMENDER_HIGH_CONFIDENCE
                else Severity.INFO
            )
            findings.append(Finding(
                Category.HINTS, severity, title, desc,
                analyzer_id="recommender", details=dict(s),
            ))
        return findings

    # ── UI state ──────────────────────────────────────────────────────

    def _set_sending_state(self, sending: bool) -> None:
        """Toggle UI between idle and sending states.

        Args:
            sending: True to show sending state, False for idle
        """
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
        self._elapsed_secs += 0.1
        self.send_button.setText(f"{self._elapsed_secs:.1f}s…")

