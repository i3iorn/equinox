"""Send, auth, and helper mixin classes for RequestPanel.

These mixins have no ``__init__`` and are purely method containers.  They rely on
``self.*`` attributes set by ``RequestPanel.__init__`` (PyQt6 MRO is respected).

Body/captures/assertions/multipart logic lives in ``body_mixin``.
"""

import logging
from typing import Dict

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QMessageBox,
    QDialog,
)

from equinox.gui.theme import Colors
from equinox.core.request import Request, Response
from equinox.core.error_enrichment import RichError, enrich_exception
from equinox.storage import Database, HistoryManager
from equinox.gui.workers import RequestWorker, DEFAULT_TIMEOUT

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# History helper (used only by _RequestSendMixin._handle_response)
# ─────────────────────────────────────────────────────────────────────────────

def _save_history_safe(db: Database, request, response=None, error=None) -> None:
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


# ─────────────────────────────────────────────────────────────────────────────
# Send / Response / Script mixin
# ─────────────────────────────────────────────────────────────────────────────

class _RequestSendMixin:
    """Methods for sending requests, handling responses, and managing the send lifecycle."""

    def _run_preflight_checks(self) -> list:
        """Return a list of advisory warning strings (empty = all clear)."""
        import re
        warnings = []
        url = self.url_input.text().strip()

        if url and "{{" not in url:
            if not re.match(r'^https?://', url, re.IGNORECASE):
                warnings.append("URL does not start with http:// or https://")

        # Check auth completeness
        from equinox.auth import BearerAuth, BasicAuth, APIKeyAuth, OAuth2Auth
        auth = self._auth or self._inherited_auth
        if auth is not None:
            if isinstance(auth, BearerAuth) and not getattr(auth, "token", None):
                warnings.append("Bearer token is empty")
            elif isinstance(auth, BasicAuth) and not getattr(auth, "username", None):
                warnings.append("Basic auth username is empty")
            elif isinstance(auth, APIKeyAuth) and not getattr(auth, "value", None):
                warnings.append("API key value is empty")
            elif isinstance(auth, OAuth2Auth) and not getattr(auth, "token_url", None):
                warnings.append("OAuth2 token URL is not configured")

        return warnings

    def _send_request(self) -> None:
        from equinox.core.interpolation import VariableInterpolator
        from equinox.storage import EnvironmentManager
        import os

        url = self.url_input.text().strip()
        if not url:
            QMessageBox.warning(self, "Missing URL", "Please enter a request URL.")
            return

        # Pre-flight advisory warnings (non-blocking)
        pf_warnings = self._run_preflight_checks()
        if pf_warnings:
            self._preflight_label.setText("  ·  ".join(pf_warnings))
            self._preflight_banner.setVisible(True)
        else:
            self._preflight_banner.setVisible(False)

        # Don't start a second request while one is in flight
        if self._worker is not None and self._worker.isRunning():
            return

        method  = self.method_combo.currentText()
        headers = self.headers_table.get_data()
        params  = self.params_table.get_enabled_data()   # only checked rows are sent
        params_list = self.params_table.get_all_rows()   # full list incl. disabled
        body_type = self.body_type_combo.currentText()
        from equinox.gui.request_panel.builder import assemble_body, inject_content_type
        body, multipart_data = assemble_body(
            body_type,
            self.body_text.toPlainText().strip(),
            self._gql_query.toPlainText().strip(),
            self._gql_vars.toPlainText().strip(),
            self._get_multipart_data(),
        )
        headers = inject_content_type(body, body_type, headers)

        # Variable interpolation
        variables: Dict[str, str] = {}
        try:
            env_mgr = EnvironmentManager(self.db)
            active = env_mgr.get_active_environment()
            if active:
                variables.update(active.get("variables", {}))
        except Exception:
            pass

        # Include inherited collection variables (groups + collection-specific)
        # These override environment variables but are overridden by OS env / session.
        if self.current_request and self.current_request.collection_id:
            try:
                from equinox.storage import CollectionManager
                col_mgr = CollectionManager(self.db)
                col_vars = col_mgr.get_all_collection_variables(
                    self.current_request.collection_id
                )
                variables.update(col_vars)
            except Exception:
                pass

        variables.update({k: v for k, v in os.environ.items() if isinstance(v, str)})
        variables.update(self._session_vars)  # captured session vars override env

        # ── Pre-request script ────────────────────────────────────────
        pre_src = self.pre_script_editor.toPlainText()
        if pre_src.strip():
            try:
                from equinox.core.scripts import ScriptRunner
                req_dict = {"method": method, "url": url,
                            "headers": dict(headers), "params": dict(params), "body": body}
                result = ScriptRunner.run_pre(pre_src, req_dict, self._session_vars)
                if result.error:
                    self.pre_script_result.setText(f"Error: {result.error}")
                    self.pre_script_result.setStyleSheet(f"color: {Colors.RED};")
                else:
                    self._session_vars.update(result.output_vars)
                    variables.update(self._session_vars)  # re-inject after script
                    self.session_vars_changed.emit(dict(self._session_vars))
                    msg = f"OK — {len(result.output_vars)} var(s) set" if result.output_vars else "OK"
                    self.pre_script_result.setText(msg)
                    self.pre_script_result.setStyleSheet(f"color: {Colors.GREEN};")
            except Exception as exc:
                logger.debug("Pre-script failed: %s", exc)

        try:
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
        except Exception as exc:
            QMessageBox.warning(self, "Variable Error",
                                f"Failed to expand variables:\n{exc}")
            return

        cert_path = self.cert_path_input.text().strip() or None
        cert_key  = self.cert_key_input.text().strip() or None

        # Resolve effective auth: own > inherited (folder > collection)
        # Re-resolve from DB at send time so tokens are always fresh.
        effective_auth = self._auth
        inherited_source = None
        if effective_auth is None and self.current_request and self.current_request.collection_id:
            try:
                from equinox.storage import CollectionManager
                collection_manager = CollectionManager(self.db)
                # Build a lightweight probe with no auth so that
                # resolve_effective_auth walks the full hierarchy
                # (folder → collection) instead of short-circuiting
                # on a previously-resolved auth baked into current_request.
                probe = Request(
                    method="GET", url="",
                    collection_id=self.current_request.collection_id,
                    folder=self.current_request.folder,
                )
                inh, inherited_source = collection_manager.resolve_effective_auth(probe)
                if inh is not None:
                    effective_auth = inh
            except Exception as exc:
                logger.debug("Send-time inherited auth resolution failed: %s", exc)
        # Fallback to cached inherited auth if DB resolution failed
        if effective_auth is None and getattr(self, "_inherited_auth", None):
            effective_auth = self._inherited_auth
            inherited_source = getattr(self, "_inherited_auth_source", None)

        # Track for post-send save-back of refreshed tokens
        self._send_inherited_auth = effective_auth if self._auth is None else None
        self._send_inherited_source = inherited_source if self._auth is None else None

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

        self._worker = RequestWorker(request, self, cookie_manager=self._cookie_manager)
        worker_ref = self._worker
        self._worker.finished.connect(
            lambda result, w=worker_ref: self._handle_response(result, w)
        )
        self._worker.start()

    def _cancel_request(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self._worker.quit()
            self._worker = None
        self._set_sending_state(False)
        self._status_message("Request cancelled", 4000)

    def _handle_response(self, result: object, worker: RequestWorker) -> None:
        # Stale result guard: only applies when _worker is still set
        if self._worker is not None and worker is not self._worker:
            return  # Stale result from a cancelled/replaced worker
        self._worker = None
        self._set_sending_state(False)

        if isinstance(result, RichError):
            logger.error(
                "Request failed: %s", result.message,
                extra={"error_type": result.exc_type,
                       "url": getattr(self.current_request, "url", ""),
                       "method": getattr(self.current_request, "method", "")},
            )
            self._status_message(f"Error: {result.message}", 8000)
            # Rich dialog: show type + message + hint about log file
            from equinox.core.log_setup import get_log_file
            log_hint = f"\n\nFull details in: {get_log_file()}" if get_log_file() else ""
            QMessageBox.critical(
                self, f"Request Failed — {result.exc_type}",
                f"{result.message}{log_hint}",
            )
            log_panel = self._logging_panel
            if log_panel:
                log_panel.log_error(self.current_request, result.message)
            _save_history_safe(self.db, self.current_request, error=result.message)
            self._persist_inherited_auth_tokens()

        elif isinstance(result, Exception):
            # Fallback for any exception that slipped through un-enriched
            rich = enrich_exception(result)
            self._handle_response(rich, worker)  # recurse once

        else:
            response: Response = result
            elapsed_ms = int(response.elapsed * 1000)
            logger.info(
                "%s %s → %d %s (%d ms)",
                response.request.method, response.request.url,
                response.status_code, response.reason, elapsed_ms,
                extra={
                    "method": response.request.method,
                    "url": response.request.url,
                    "status": response.status_code,
                    "elapsed_ms": elapsed_ms,
                    "size_bytes": response.size,
                },
            )
            self._status_message(
                f"{response.status_code} {response.reason}  —  {elapsed_ms} ms", 8000
            )
            self.response_received.emit(response)
            self._apply_captures(response)
            self._evaluate_assertions(response)
            self._run_post_script(response)
            self._refresh_url_completer()
            log_panel = self._logging_panel
            if log_panel:
                log_panel.log_response(self.current_request, response)
            _save_history_safe(self.db, self.current_request, response)

            # Save refreshed inherited-auth tokens back to collection/folder
            self._persist_inherited_auth_tokens()

    def _apply_captures(self, response: Response) -> None:
        """Run capture rules against the response and update session vars."""
        try:
            from equinox.core.captures import CaptureEngine
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
            from equinox.core.scripts import ScriptRunner
            resp_dict: Dict = {
                "status_code": response.status_code,
                "headers": dict(response.headers),
                "body": response.text if hasattr(response, "text") else "",
                "json": None,
            }
            try:
                resp_dict["json"] = response.json()
            except Exception:
                pass
            script_result = ScriptRunner.run_post(
                post_src, resp_dict, self._session_vars
            )
            if script_result.error:
                self.post_script_result.setText(f"Error: {script_result.error}")
                self.post_script_result.setStyleSheet(f"color: {Colors.RED};")
            else:
                self._session_vars.update(script_result.output_vars)
                self.session_vars_changed.emit(dict(self._session_vars))
                msg = (
                    f"OK — {len(script_result.output_vars)} var(s) set"
                    if script_result.output_vars else "OK"
                )
                self.post_script_result.setText(msg)
                self.post_script_result.setStyleSheet(f"color: {Colors.GREEN};")
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
        from equinox.auth import OAuth2Auth
        if not isinstance(auth, OAuth2Auth):
            return
        # Only write back if the object now has a token (i.e. apply() ran)
        if not auth.access_token:
            return
        try:
            from equinox.storage import CollectionManager
            mgr = CollectionManager(self.db)
            req = self.current_request
            if not req or not req.collection_id:
                return
            if source == "collection":
                mgr.set_collection_auth(req.collection_id, auth)
            elif source.startswith("folder:"):
                folder_path = source[7:]
                mgr.set_folder_auth(req.collection_id, folder_path, auth)
            # Update display to show the fresh token info
            self._inherited_auth = auth
            self._inherited_auth_source = source
            self._update_auth_display(self._auth)
        except Exception as exc:
            logger.debug("Failed to persist inherited auth tokens: %s", exc)

    def _set_sending_state(self, sending: bool) -> None:
        if sending:
            self._elapsed_secs = 0.0
            self._elapsed_timer.start()
            self.send_button.setEnabled(False)
            self.send_button.setText("0.0s…")
            self.cancel_button.setVisible(True)
            self.url_input.setEnabled(False)
            self.method_combo.setEnabled(False)
        else:
            self._elapsed_timer.stop()
            self.send_button.setEnabled(True)
            self.send_button.setText("Send")
            self.cancel_button.setVisible(False)
            self.url_input.setEnabled(True)
            self.method_combo.setEnabled(True)

    def _tick_elapsed(self) -> None:
        self._elapsed_secs += 0.1
        self.send_button.setText(f"{self._elapsed_secs:.1f}s…")


# ─────────────────────────────────────────────────────────────────────────────
# Auth mixin
# ─────────────────────────────────────────────────────────────────────────────

class _RequestAuthMixin:
    """Methods for authentication configuration and display."""

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
        if dialog.exec() == QDialog.DialogCode.Accepted:
            if hasattr(dialog, '_saved_auth'):
                saved = dialog._saved_auth
                # If the request was using inherited auth and the user saved
                # without meaningful changes, keep self._auth = None so the
                # request continues inheriting from the collection/folder.
                if was_inherited and saved is not None and self._auth_configs_match(saved, self._inherited_auth):
                    return
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
            for key in (
                "has_access_token", "has_refresh_token", "expires_at",
                "access_token", "refresh_token", "token_timeout",
            ):
                d1.pop(key, None)
                d2.pop(key, None)
            return d1 == d2
        except Exception:
            return False

    def _resolve_inherited_auth(self) -> None:
        """Re-resolve inherited auth from the collection/folder hierarchy.

        Called after clearing own auth, after the auth dialog sets "No Auth",
        and when the collection's auth configuration changes externally.
        """
        self._inherited_auth = None
        self._inherited_auth_source = None
        if self.current_request and getattr(self.current_request, "collection_id", None):
            try:
                from equinox.storage import CollectionManager
                mgr = CollectionManager(self.db)
                probe = Request(
                    method="GET", url="",
                    collection_id=self.current_request.collection_id,
                    folder=getattr(self.current_request, "folder", None),
                )
                inh_auth, inh_source = mgr.resolve_effective_auth(probe)
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

    def _update_auth_display(self, auth=None) -> None:
        from equinox.auth import BasicAuth, OAuth2Auth, BearerAuth, APIKeyAuth
        self.auth_status_label.setText("")
        self.auth_status_label.setStyleSheet("")

        # If no own auth, check inherited
        display_auth = auth
        inherited_label = ""
        if not display_auth and getattr(self, "_inherited_auth", None):
            display_auth = self._inherited_auth
            source = getattr(self, "_inherited_auth_source", "") or ""
            if source.startswith("folder:"):
                inherited_label = f"  (inherited from folder \"{source[7:]}\")"
            elif source == "collection":
                inherited_label = "  (inherited from collection)"

        if not display_auth:
            self.auth_type_label.setText("Auth: None")
            self.auth_details_label.setText("No authentication configured")
        elif isinstance(display_auth, BasicAuth):
            self.auth_type_label.setText(f"Auth: Basic{inherited_label}")
            self.auth_details_label.setText(f"Username: {display_auth.username}")
        elif isinstance(display_auth, BearerAuth):
            preview = display_auth.token[:8] + "…" if len(display_auth.token) > 8 else "***"
            self.auth_type_label.setText(f"Auth: Bearer Token{inherited_label}")
            self.auth_details_label.setText(f"Token: {preview}")
        elif isinstance(display_auth, OAuth2Auth):
            from datetime import datetime, timezone
            self.auth_type_label.setText(f"Auth: OAuth 2.0{inherited_label}")
            self.auth_details_label.setText(
                f"Token URL: {display_auth.token_url or '—'}\nClient ID: {display_auth.client_id or '—'}"
            )
            info = display_auth.get_token_info()
            if not display_auth.access_token:
                text, color = "Token: None", Colors.RED
            elif info["needs_refresh"]:
                text, color = f"Token: Expiring soon  [{info['access_token']}]", Colors.AMBER
            else:
                text, color = f"Token: Valid  [{info['access_token']}]", Colors.GREEN
            if info["expires_at"]:
                try:
                    secs = int((datetime.fromisoformat(info["expires_at"]) -
                                datetime.now(timezone.utc).replace(tzinfo=None)).total_seconds())
                    text += f"  (expires in {secs}s)" if secs > 0 else "  (expired)"
                except Exception:
                    pass
            self.auth_status_label.setText(text)
            self.auth_status_label.setStyleSheet(f"color: {color};")
        elif isinstance(display_auth, APIKeyAuth):
            preview = display_auth.value[:4] + "…" if len(display_auth.value) > 4 else "***"
            self.auth_type_label.setText(f"Auth: API Key{inherited_label}")
            self.auth_details_label.setText(f"{display_auth.key} = {preview}  ({display_auth.location})")
        else:
            # Unknown auth type (e.g. AWS SigV4)
            type_name = type(display_auth).__name__
            self.auth_type_label.setText(f"Auth: {type_name}{inherited_label}")
            self.auth_details_label.setText("")

