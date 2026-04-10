"""Send, auth, and helper mixin classes for RequestPanel.

These mixins have no ``__init__`` and are purely method containers.  They rely on
``self.*`` attributes set by ``RequestPanel.__init__`` (PyQt6 MRO is respected).

Body/captures/assertions/multipart logic lives in ``body_mixin``.
"""

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QMessageBox,
    QDialog,
)

from equinox.auth import BearerAuth, BasicAuth, APIKeyAuth, OAuth2Auth
from equinox.core.captures import CaptureEngine
from equinox.core.error_enrichment import RichError, enrich_exception
from equinox.core.interpolation import VariableInterpolator, collect_interpolation_variables
from equinox.core.log_setup import get_log_file
from equinox.core.redact import mask_secret
from equinox.core.request import Request, Response
from equinox.core.scripts import ScriptRunner
from equinox.core.time import utc_now
from equinox.gui.request_panel.builder import assemble_body, inject_content_type
from equinox.gui.theme import Colors
from equinox.gui.workers import RequestWorker
from equinox.storage import Database, HistoryManager

logger = logging.getLogger(__name__)

# Prefix used when encoding the auth inheritance source as a string.
# e.g. "folder:Api/v2" means the auth came from the folder named "Api/v2".
_FOLDER_AUTH_PREFIX = "folder:"

# Keys excluded from auth config comparison (volatile token state).
_AUTH_VOLATILE_KEYS = frozenset({
    "has_access_token", "has_refresh_token", "expires_at",
    "access_token", "refresh_token", "token_timeout",
})

# Pre-compiled URL scheme regex for preflight checks.
_HTTP_SCHEME_RE = re.compile(r'^https?://', re.IGNORECASE)

# Auth-type preflight checks: (type, attribute_to_check, warning_message).
_AUTH_PREFLIGHT_CHECKS: Tuple[Tuple[type, str, str], ...] = (
    (BearerAuth, "token", "Bearer token is empty"),
    (BasicAuth, "username", "Basic auth username is empty"),
    (APIKeyAuth, "value", "API key value is empty"),
    (OAuth2Auth, "token_url", "OAuth2 token URL is not configured"),
)

# Auth display dispatch: (auth_type, method_name) — looked up by _update_auth_display.
_AUTH_DISPLAY_DISPATCH: Tuple[Tuple[type, str], ...] = (
    (BasicAuth, "_display_basic_auth"),
    (BearerAuth, "_display_bearer_auth"),
    (OAuth2Auth, "_display_oauth2_auth"),
    (APIKeyAuth, "_display_apikey_auth"),
)


# ─────────────────────────────────────────────────────────────────────────────
# Module-level helpers
# ─────────────────────────────────────────────────────────────────────────────

def _save_history_safe(
    db: Database,
    request: Request,
    response: Optional[Response] = None,
    error: Optional[str] = None,
) -> None:
    """Save to history without letting exceptions bubble to the UI."""
    if request is None:
        return
    try:
        mgr = HistoryManager(db)
        if response is not None:
            mgr.save_history(request, response)
        elif error is not None:
            mgr.save_history(request, error=error)
    except Exception:
        logger.debug("Failed to save history", exc_info=True)


def _write_auth_to_source(mgr, collection_id: int, source: str, auth) -> None:
    """Persist *auth* to the collection or folder identified by *source*.

    Shared by ``_persist_inherited_auth_tokens`` and
    ``_save_inherited_token_to_source`` so the source→manager dispatch
    is defined in exactly one place.
    """
    if source == "collection":
        mgr.set_collection_auth(collection_id, auth)
    elif source.startswith(_FOLDER_AUTH_PREFIX):
        mgr.set_folder_auth(collection_id, source[len(_FOLDER_AUTH_PREFIX):], auth)


# ─────────────────────────────────────────────────────────────────────────────
# Send / Response / Script mixin
# ─────────────────────────────────────────────────────────────────────────────

class _RequestSendMixin:
    """Methods for sending requests, handling responses, and managing the send lifecycle."""

    # ── Preflight ─────────────────────────────────────────────────────

    def _run_preflight_checks(self) -> List[str]:
        """Return a list of advisory warning strings (empty = all clear)."""
        warnings: List[str] = []
        url = self.url_input.text().strip()

        if url and "{{" not in url and not _HTTP_SCHEME_RE.match(url):
            warnings.append("URL does not start with http:// or https://")

        auth = self._auth or self._inherited_auth
        if auth is not None:
            for auth_type, attr, msg in _AUTH_PREFLIGHT_CHECKS:
                if isinstance(auth, auth_type) and not getattr(auth, attr, None):
                    warnings.append(msg)
                    break

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
        variables: Dict[str, str],
    ) -> Tuple[str, Dict[str, str], Dict[str, str], Optional[str]]:
        """Interpolate ``{{VAR}}`` placeholders in all request fields.

        Returns ``(url, headers, params, body)`` with variables expanded.
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
        if body:
            body = VariableInterpolator.interpolate(body, variables)
        logger.debug("Variable interpolation completed successfully")
        return url, headers, params, body

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
        url = self.url_input.text().strip()
        if not url:
            QMessageBox.warning(self, "Missing URL", "Please enter a request URL.")
            return

        logger.debug("_send_request() initiated: url=%s", url[:80])

        # Pre-flight advisory warnings (non-blocking)
        pf_warnings = self._run_preflight_checks()
        if pf_warnings:
            self._preflight_label.setText("  ·  ".join(pf_warnings))
            self._preflight_banner.setVisible(True)
            logger.debug("Preflight warnings: %s", pf_warnings)
        else:
            self._preflight_banner.setVisible(False)

        # Don't start a second request while one is in flight
        if self._worker is not None and self._worker.isRunning():
            return

        method = self.method_combo.currentText()
        headers = self.headers_table.get_data()
        params = self.params_table.get_enabled_data()   # only checked rows are sent
        params_list = self.params_table.get_all_rows()   # full list incl. disabled
        body_type = self.body_type_combo.currentText()
        body, multipart_data = assemble_body(
            body_type,
            self.body_text.toPlainText().strip(),
            self._gql_query.toPlainText().strip(),
            self._gql_vars.toPlainText().strip(),
            self._get_multipart_data(),
        )
        headers = inject_content_type(body, body_type, headers)

        # Variable interpolation — delegate to the canonical shared helper so
        # the GUI and CLI always use the same resolution order.
        variables = collect_interpolation_variables(
            self.db,
            collection_id=getattr(self.current_request, "collection_id", None),
            session_vars=self._session_vars,
        )

        # Pre-request script (may update variables in-place)
        variables = self._run_pre_script(method, url, headers, params, body, variables)

        try:
            url, headers, params, body = self._interpolate_request_fields(
                url, headers, params, body, variables,
            )
        except Exception as exc:
            logger.warning("Variable interpolation failed: %s", exc)
            QMessageBox.warning(
                self, "Variable Error", f"Failed to expand variables:\n{exc}",
            )
            return

        cert_path = self.cert_path_input.text().strip() or None
        cert_key = self.cert_key_input.text().strip() or None

        # Resolve effective auth: own > inherited (folder > collection)
        effective_auth, inherited_source = self._resolve_send_auth()

        # Track for post-send save-back of refreshed tokens
        is_inherited = self._auth is None
        self._send_inherited_auth = effective_auth if is_inherited else None
        self._send_inherited_source = inherited_source if is_inherited else None

        # Carry forward collection context from the loaded request so that
        # inherited auth, collection variables, and autosave keep working
        # even after the first send replaces self.current_request.
        _prev = self.current_request
        request = Request(
            method=method, url=url, headers=headers,
            params=params, params_list=params_list,
            body=body, auth=effective_auth,
            timeout=self.timeout_spin.value(),
            verify_ssl=self.verify_ssl_check.isChecked(),
            follow_redirects=self.follow_redirects_check.isChecked(),
            captures=self._get_captures(),
            pre_script=self.pre_script_editor.toPlainText(),
            post_script=self.post_script_editor.toPlainText(),
            cert_path=cert_path,
            cert_key_path=cert_key,
            multipart_data=multipart_data,
            collection_id=getattr(_prev, "collection_id", None),
            folder=getattr(_prev, "folder", None),
            id=getattr(_prev, "id", None),
            name=getattr(_prev, "name", None),
            path_params=self.path_params_table.get_all_data(),
        )
        self.current_request = request

        logger.info(
            "Sending %s %s", method, url,
            extra={"method": method, "url": url},
        )
        log_panel = self._logging_panel
        if log_panel:
            log_panel.log_request(request)

        self.request_sent.emit(request)
        self._set_sending_state(True)
        # NOTE: Do NOT call _clear_dirty() here.  Sending is not a save —
        # the user's edits must still be autosaved to the DB when navigating
        # away.  Clearing the flag here would silently discard changes.

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
        worker = self._worker
        if worker is not None:
            worker.cancel()
            worker.quit()
            worker.wait(2000)  # bounded wait prevents orphaned thread signals
            self._worker = None
        self._set_sending_state(False)
        self._status_message("Request cancelled", 4000)

    # ── Response handling ─────────────────────────────────────────────

    def _handle_response(self, result: object, worker: RequestWorker) -> None:
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
        self._status_message(f"Error: {result.message}", 8000)

        # Rich dialog: show type + message + hint about log file
        from equinox.gui.widgets import CopyableMessageBox
        log_hint = f"\n\nFull details in: {get_log_file()}" if get_log_file() else ""
        CopyableMessageBox.critical(
            self, f"Request Failed — {result.exc_type}",
            f"{result.message}{log_hint}",
            copy_text=result.tb,
        )

        log_panel = self._logging_panel
        if log_panel:
            log_panel.log_error(_sent_request, result.message)

        # Defer DB write and recommender so the UI updates first.
        _err_msg = result.message
        QTimer.singleShot(0, lambda: _save_history_safe(self.db, _sent_request, error=_err_msg))
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
            f"{response.status_code} {response.reason}  —  {elapsed_ms} ms", 8000,
        )
        self.response_received.emit(response)
        self._apply_captures(response)
        self._evaluate_assertions(response)
        self._run_post_script(response)
        self._refresh_url_completer()

        log_panel = self._logging_panel
        if log_panel:
            log_panel.log_response(_sent_request, response)

        # Defer DB write so the UI renders the response instantly.
        QTimer.singleShot(0, lambda: _save_history_safe(self.db, _sent_request, response))

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
            _write_auth_to_source(self._collection_mgr, req.collection_id, source, auth)
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
                top_n=5,
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
                    "Suggestions available (open Intelligence panel)", 8000,
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
                Severity.WARNING if s.get("confidence", 0) >= 0.75
                else Severity.INFO
            )
            findings.append(Finding(
                Category.HINTS, severity, title, desc,
                analyzer_id="recommender", details=dict(s),
            ))
        return findings

    def _set_sending_state(self, sending: bool) -> None:
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


# ─────────────────────────────────────────────────────────────────────────────
# Auth mixin
# ─────────────────────────────────────────────────────────────────────────────

class _RequestAuthMixin:
    """Methods for authentication configuration and display."""

    def _save_inherited_token_to_source(self, auth) -> None:
        """Write a freshly-fetched token back to the collection or folder it came from.

        Used by :meth:`_configure_auth` when the user fetches a token via the
        auth dialog while the request is still using *inherited* auth.  The
        token belongs to the collection/folder, not to the request.
        """
        source = getattr(self, "_inherited_auth_source", None)
        if not source:
            return
        req = self.current_request
        if not req or not req.collection_id:
            return
        try:
            _write_auth_to_source(self._collection_mgr, req.collection_id, source, auth)
            logger.debug("Saved dialog-fetched token to %s", source)
        except Exception as exc:
            logger.debug("Failed to save dialog token to source: %s", exc)

    def _create_auth_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(6, 8, 6, 8)
        self.auth_type_label = QLabel("Auth: None")
        self.auth_type_label.setStyleSheet("font-weight: bold;")
        self.auth_details_label = QLabel("No authentication configured")
        self.auth_details_label.setObjectName("mutedLabel")
        self.auth_details_label.setWordWrap(True)
        self.auth_status_label = QLabel("")
        self.auth_status_label.setWordWrap(True)
        configure_btn = QPushButton("Configure Authentication…")
        configure_btn.clicked.connect(self._configure_auth)
        clear_btn = QPushButton("Clear Auth")
        clear_btn.clicked.connect(self._clear_auth)
        btn_row = QHBoxLayout()
        btn_row.addWidget(configure_btn)
        btn_row.addWidget(clear_btn)
        btn_row.addStretch()
        layout.addWidget(self.auth_type_label)
        layout.addWidget(self.auth_details_label)
        layout.addWidget(self.auth_status_label)
        layout.addLayout(btn_row)
        layout.addStretch()
        return w

    def _configure_auth(self) -> None:
        from equinox.gui.dialogs.auth_dialog import AuthDialog
        # Show inherited auth in the dialog so the user sees what's active
        was_inherited = self._auth is None and self._inherited_auth is not None
        display_auth = self._auth or self._inherited_auth
        dialog = AuthDialog(display_auth, self, db=self.db)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if not hasattr(dialog, '_saved_auth'):
            return

        saved = dialog._saved_auth
        fetched_token = getattr(dialog, '_last_fetched_auth', None)

        # ── Guard: don't accidentally bake inherited auth into the request ──
        #
        # If the request was using inherited auth (from collection/folder)
        # and the user opened the dialog without changing the underlying
        # configuration, we must NOT set self._auth — that would store a
        # copy of the collection's auth on the request row.
        if was_inherited and saved is not None:
            configs_match = self._auth_configs_match(saved, self._inherited_auth)

            # Case A — unchanged config, no token fetch: no-op.
            if configs_match and not fetched_token:
                return

            # Case B — unchanged config, token fetched: persist at source.
            if configs_match and fetched_token is not None:
                self._inherited_auth = saved
                self._save_inherited_token_to_source(saved)
                self._update_auth_display(self._auth)  # self._auth still None
                return

        # Case C — user explicitly set a different auth (or changed the
        # config): honour it as own auth on the request.
        old_auth = self._auth
        self._auth = saved
        if self._auth is not None:
            # Own auth supersedes inherited
            self._inherited_auth = None
            self._inherited_auth_source = None
        else:
            # User chose "No Auth" — re-resolve inherited
            self._resolve_inherited_auth()
        self._update_auth_display(self._auth)
        # Mark dirty if auth actually changed
        if not self._auth_configs_match(old_auth, self._auth):
            self._mark_dirty()

    def _clear_auth(self) -> None:
        had_auth = self._auth is not None
        self._auth = None
        self._resolve_inherited_auth()
        self._update_auth_display(None)
        if had_auth:
            self._mark_dirty()

    @staticmethod
    def _auth_configs_match(a, b) -> bool:
        """Return True if two auth objects have the same configuration.

        Excludes volatile / token-state fields that change without user action
        so that, e.g., a token refresh does not make the dialog think the user
        changed the inherited auth configuration.
        """
        if a is None and b is None:
            return True
        if a is None or b is None:
            return False
        try:
            d1 = a.to_dict()
            d2 = b.to_dict()
            for key in _AUTH_VOLATILE_KEYS:
                d1.pop(key, None)
                d2.pop(key, None)
            return d1 == d2
        except Exception:
            logger.warning("Auth config comparison failed", exc_info=True)
            return False

    def _resolve_inherited_auth(self) -> None:
        """Re-resolve inherited auth from the collection/folder hierarchy.

        Called after clearing own auth, after the auth dialog sets "No Auth",
        and when the collection's auth configuration changes externally.
        """
        self._inherited_auth = None
        self._inherited_auth_source = None
        probe = self._build_auth_probe()
        if probe is None:
            return
        try:
            inh_auth, inh_source = self._collection_mgr.resolve_effective_auth(probe)
            if inh_auth is not None:
                self._inherited_auth = inh_auth
                self._inherited_auth_source = inh_source
        except Exception as exc:
            logger.debug("Failed to resolve inherited auth: %s", exc)

    def refresh_inherited_auth(self) -> None:
        """Public method for external callers (e.g. window signal wiring)
        to trigger an inherited-auth refresh and update the display."""
        if self._auth is None:
            self._resolve_inherited_auth()
            self._update_auth_display(self._auth)

    @staticmethod
    def _format_inherited_label(source: Optional[str]) -> str:
        """Build a human-readable '(inherited from …)' suffix."""
        if not source:
            return ""
        if source.startswith(_FOLDER_AUTH_PREFIX):
            folder = source[len(_FOLDER_AUTH_PREFIX):]
            return f'  (inherited from folder "{folder}")'
        if source == "collection":
            return "  (inherited from collection)"
        return ""

    def _update_auth_display(self, auth: Any = None) -> None:
        self.auth_status_label.setText("")
        self.auth_status_label.setStyleSheet("")

        # If no own auth, check inherited
        display_auth = auth
        inherited_label = ""
        if not display_auth and getattr(self, "_inherited_auth", None):
            display_auth = self._inherited_auth
            inherited_label = self._format_inherited_label(
                getattr(self, "_inherited_auth_source", None),
            )

        if not display_auth:
            self.auth_type_label.setText("Auth: None")
            self.auth_details_label.setText("No authentication configured")
            return

        for auth_type, method_name in _AUTH_DISPLAY_DISPATCH:
            if isinstance(display_auth, auth_type):
                getattr(self, method_name)(display_auth, inherited_label)
                return

        # Unknown auth type (e.g. AWS SigV4)
        type_name = type(display_auth).__name__
        self.auth_type_label.setText(f"Auth: {type_name}{inherited_label}")
        self.auth_details_label.setText("")

    def _display_basic_auth(self, auth: BasicAuth, inherited_label: str) -> None:
        """Populate the auth display labels for Basic authentication."""
        self.auth_type_label.setText(f"Auth: Basic{inherited_label}")
        self.auth_details_label.setText(f"Username: {auth.username}")

    def _display_bearer_auth(self, auth: BearerAuth, inherited_label: str) -> None:
        """Populate the auth display labels for Bearer token authentication."""
        preview = mask_secret(auth.token)
        self.auth_type_label.setText(f"Auth: Bearer Token{inherited_label}")
        self.auth_details_label.setText(f"Token: {preview}")

    def _display_apikey_auth(self, auth: APIKeyAuth, inherited_label: str) -> None:
        """Populate the auth display labels for API Key authentication."""
        preview = auth.value[:4] + "…" if len(auth.value) > 4 else "***"
        self.auth_type_label.setText(f"Auth: API Key{inherited_label}")
        self.auth_details_label.setText(
            f"{auth.key} = {preview}  ({auth.location})"
        )

    def _display_oauth2_auth(self, auth: OAuth2Auth, inherited_label: str) -> None:
        """Populate the auth display labels for an OAuth 2.0 configuration."""
        self.auth_type_label.setText(f"Auth: OAuth 2.0{inherited_label}")
        self.auth_details_label.setText(
            f"Token URL: {auth.token_url or '—'}\nClient ID: {auth.client_id or '—'}"
        )
        info = auth.get_token_info()
        if not auth.access_token:
            text, color = "Token: None", Colors.RED
        elif info["needs_refresh"]:
            text, color = f"Token: Expiring soon  [{info['access_token']}]", Colors.AMBER
        else:
            text, color = f"Token: Valid  [{info['access_token']}]", Colors.GREEN
        if info["expires_at"]:
            try:
                secs = int((datetime.fromisoformat(info["expires_at"]) -
                            utc_now()).total_seconds())
                text += f"  (expires in {secs}s)" if secs > 0 else "  (expired)"
            except Exception:
                logger.debug("Failed to parse OAuth2 token expiry", exc_info=True)
        self.auth_status_label.setText(text)
        self.auth_status_label.setStyleSheet(f"color: {color};")

