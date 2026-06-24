"""Send orchestration mixin for ``RequestPanel``."""
from __future__ import annotations

import logging
from typing import Any
from typing import cast
from typing import TYPE_CHECKING

from equinox.application.requests import build_preflight_issues
from equinox.application.requests import issues_to_messages
from equinox.application.requests import prepare_send
from equinox.gui.error_presenter import ErrorPresenter
from equinox.gui.logging_utils import notify_log_panel
from equinox.gui.request_panel._mixins.send_response_mixin import SendResponseMixin
from equinox.gui.request_panel._mixins.send_worker_mixin import SendWorkerMixin
from equinox.security import redact_url
from PyQt6.QtWidgets import QWidget

logger = logging.getLogger(__name__)


class _RequestSendMixin(SendWorkerMixin, SendResponseMixin):  # type: ignore[misc]
    """Orchestrate request preparation, worker dispatch, and response handling."""

    url_input: Any
    verify_ssl_check: Any
    follow_redirects_check: Any
    pre_script_editor: Any
    post_script_editor: Any
    request_sent: Any
    pre_script_result: Any
    db: Any
    _auth: Any | None
    _inherited_auth: Any | None
    _logging_panel: Any

    if TYPE_CHECKING:

        def get_policy_profile(self) -> str: ...
        def _build_request_editor_snapshot(self) -> Any: ...

    def _as_qwidget(self) -> QWidget:
        return cast(QWidget, cast(object, self))

    def _run_preflight_checks(self) -> list[str]:
        """Return advisory warnings for the current editor state."""
        issues = build_preflight_issues(
            url=self.url_input.text().strip(),
            policy_profile=self.get_policy_profile(),
            verify_ssl=self.verify_ssl_check.isChecked(),
            follow_redirects=self.follow_redirects_check.isChecked(),
            pre_script=self.pre_script_editor.toPlainText(),
            post_script=self.post_script_editor.toPlainText(),
            auth=self._auth or self._inherited_auth,
        )
        return [str(m) for m in issues_to_messages(issues)]

    def _send_request(self) -> None:
        """Prepare the current editor snapshot and dispatch the request worker."""
        snapshot = self._build_request_editor_snapshot()
        if not self._ensure_sendable_url(snapshot.url):
            return
        logger.debug("_send_request() initiated: url=%s", redact_url(snapshot.url)[:80])
        self._display_preflight_warnings()
        if not self._strict_policy_allows_send(snapshot):
            return
        if self._worker is not None and self._worker.isRunning():
            return
        result = self._prepare_send_result(snapshot)
        if not result.ready:
            self._present_blocking_issues(result.blocking_issues)
            return
        self._dispatch_prepared_request(result.package, snapshot)

    def _strict_policy_allows_send(self, snapshot: Any) -> bool:
        """Enforce strict-profile send blockers before request preparation."""
        if str(self.get_policy_profile()).lower() != "strict":
            return True
        if snapshot.url.lower().startswith("http://"):
            ErrorPresenter.warning(
                self._as_qwidget(),
                "Strict policy blocks insecure HTTP requests. Use https:// instead.",
                title="Strict Policy",
            )
            return False
        if snapshot.verify_ssl:
            return True
        ErrorPresenter.warning(
            self._as_qwidget(),
            "Strict policy requires SSL certificate verification.",
            title="Strict Policy",
        )
        return False

    def _prepare_send_result(self, snapshot: Any) -> Any:
        """Delegate request preparation to the application-layer send service."""
        return prepare_send(
            snapshot=snapshot,
            db=self.db,
            collection_manager=getattr(self, "_request_persistence", None),
            own_auth=self._auth,
            inherited_auth=getattr(self, "_inherited_auth", None),
            inherited_auth_source=getattr(self, "_inherited_auth_source", None),
            policy_profile=self.get_policy_profile(),
        )

    def _present_blocking_issues(self, issues: tuple[Any, ...]) -> None:
        """Render blocking send issues returned by the application layer."""
        titles = {
            "variables.unresolved": "Variable Error",
            "interpolation.failed": "Variable Error",
            "auth.interpolation_failed": "Variable Error",
            "body.assembly_failed": "Request Validation",
            "request.construction_failed": "Request Error",
        }
        for issue in issues:
            ErrorPresenter.warning(
                self._as_qwidget(),
                issue.message,
                title=titles.get(issue.code, "Request Error"),
            )

    def _dispatch_prepared_request(self, package: Any, snapshot: Any) -> None:
        """Apply prepared-send side effects and start the worker."""
        self._apply_prepared_script_state(package, snapshot)
        self._track_prepared_auth_state(package)
        request = package.request
        self.current_request = request
        logger.info("Sending %s %s", request.method, request.url, extra={"method": request.method, "url": redact_url(request.url)})
        notify_log_panel(self._logging_panel, "log_request", request)
        self.request_sent.emit(request)
        self._set_sending_state(True)
        self._dispatch_worker(request)

    def _apply_prepared_script_state(self, package: Any, snapshot: Any) -> None:
        """Render pre-script results and merge script-set session variables."""
        if package.pre_script_result is not None:
            self._display_script_result(self.pre_script_result, package.pre_script_result)
            self._apply_script_vars(package.pre_script_result)
            return
        if str(self.get_policy_profile()).lower() == "strict" and snapshot.pre_script.strip():
            self.pre_script_result.setText("Skipped by strict policy")

    def _track_prepared_auth_state(self, package: Any) -> None:
        """Remember inherited auth details for post-response token persistence."""
        self._send_inherited_auth = package.request.auth if package.is_auth_inherited else None
        self._send_inherited_source = (
            package.inherited_auth_source if package.is_auth_inherited else None
        )
