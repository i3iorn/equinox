"""Autosave and dirty-state helpers for RequestPanel."""
from __future__ import annotations

import logging
from typing import Any
from typing import TYPE_CHECKING

from equinox.application.requests._assembly import assemble_body
from equinox.core.request import Request
from equinox.gui.request_panel._constants import STATUS_DURATION_LONG
from equinox.security.redactor import redact_url

logger = logging.getLogger(__name__)


class RequestAutosaveMixin:
    """Encapsulates request editor serialization and autosave behavior."""

    _dirty: bool
    _auth: Any
    current_request: Any
    _request_persistence: Any

    if TYPE_CHECKING:

        def window(self) -> Any: ...
        def _build_request_editor_snapshot(self) -> Any: ...
        def _clear_dirty(self) -> None: ...
        def _save_request(self) -> bool: ...

    def _sync_dirty_state_ui(self) -> None:
        """Refresh any optional dirty-state UI affordances."""
        try:
            sync = getattr(self, "_sync_editor_state_ui", None)
            if callable(sync):
                sync()
        except Exception:
            logger.debug("Failed to refresh dirty-state UI", exc_info=True)

    def is_dirty(self) -> bool:
        return self._dirty

    def _mark_dirty(self) -> None:
        self._dirty = True
        self._sync_dirty_state_ui()

    def _status_message(self, text: str, timeout_ms: int = 5000) -> None:
        """Show a message in the main window status bar (best-effort)."""
        try:
            self.window().statusBar().showMessage(text, timeout_ms)
        except Exception:
            logger.warning("Could not show status message: %s", text)

    def _build_request_from_editor(self, **overrides: Any) -> Request:
        """Construct a Request from the current editor widget state."""
        snapshot = self._build_request_editor_snapshot()
        body, multipart_data = assemble_body(
            snapshot.body_type,
            snapshot.body.strip(),
            snapshot.graphql_query.strip(),
            snapshot.graphql_variables.strip(),
            list(snapshot.multipart_data),
        )
        fields = dict(
            method=snapshot.method,
            url=snapshot.url,
            headers=snapshot.headers,
            params=snapshot.params,
            params_list=list(snapshot.params_list),
            body=body,
            auth=self._auth,
            timeout=snapshot.timeout,
            verify_ssl=snapshot.verify_ssl,
            follow_redirects=snapshot.follow_redirects,
            name=snapshot.name,
            description=snapshot.description,
            collection_id=snapshot.collection_id,
            folder=snapshot.folder,
            id=snapshot.request_id,
            captures=list(snapshot.captures),
            assertions=list(snapshot.assertions),
            multipart_data=multipart_data,
            pre_script=snapshot.pre_script,
            post_script=snapshot.post_script,
            cert_path=snapshot.cert_path,
            cert_key_path=snapshot.cert_key_path,
            path_params=snapshot.path_params,
        )
        fields.update(overrides)
        return Request(**fields)

    def autosave_current(self) -> None:
        """Persist the current editor state back to the DB if dirty."""
        if not self._dirty:
            return
        req = self.current_request
        if not req or not getattr(req, "id", None):
            return
        try:
            updated = self._build_request_from_editor(
                name=req.name,
                collection_id=req.collection_id,
                folder=req.folder,
                id=req.id,
            )
            self._request_persistence.autosave_request(updated)
            self._clear_dirty()
            logger.debug("Autosaved request id=%s %s %s", req.id, updated.method, redact_url(updated.url))
        except Exception:
            logger.error(
                "Autosave failed for request id=%s", getattr(req, "id", None), exc_info=True,
            )
            self._status_message(
                "Autosave failed - click Save to preserve changes", STATUS_DURATION_LONG,
            )

    def save_current_request(self) -> bool:
        """Public wrapper for the save dialog flow."""
        return self._save_request()
