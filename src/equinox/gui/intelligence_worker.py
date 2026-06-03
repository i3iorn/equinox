"""Background worker for Response Intelligence analysis."""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any
from typing import TypeVar

from equinox.core.request import Request
from equinox.core.request import Response
from equinox.core.response_intelligence import AnalysisContext
from equinox.core.response_intelligence import AnalysisEngine
from equinox.core.response_intelligence import Finding
from equinox.core.response_intelligence import normalize_url_pattern
from equinox.core.response_intelligence import SchemaDriftAnalyzer
from equinox.intelligence import Recommender
from equinox.intelligence import suggestions_to_findings
from equinox.storage import Database
from equinox.storage.response_intelligence import ResponseIntelligenceManager
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtCore import QObject
from PyQt6.QtCore import QThread

__all__ = ["IntelligenceWorker"]

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


class IntelligenceWorker(QThread):
    """Run response intelligence analysis on a background thread.

    Emits ``finished(list)`` with ``list[Finding]`` on completion.
    Emits ``finished([])`` if the analysis fails or is cancelled so the UI
    is never left waiting for a signal that never arrives.

    After a successful analysis, endpoint stats and schema snapshots are
    updated in the database.

    Args:
        request: The request that was sent.
        response: The response that was received.
        db: Open database instance (``equinox.storage.Database``).
        disabled_analyzers: Names of analyzers to skip.  Stored internally
            as a ``frozenset`` so the caller cannot mutate it after
            construction.  Non-string or empty entries are silently dropped.
        parent: Optional Qt parent object.
    """

    finished = pyqtSignal(list)  # list[Finding]
    _RECOMMENDER_ANALYZER_ID = "recommender"

    # ── Construction ──────────────────────────────────────────────────────────

    def __init__(
        self,
        request: Request,
        response: Response,
        db: Database,
        disabled_analyzers: set[str] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._request = request
        self._response = response
        self._db = db
        # Only accept non-empty strings; anything else is silently dropped so
        # a malformed caller cannot accidentally disable every analyzer.
        self._disabled: frozenset[str] = frozenset(
            a for a in (disabled_analyzers or ()) if isinstance(a, str) and a
        )

    # ── QThread entry point ───────────────────────────────────────────────────

    def run(self) -> None:
        """Execute analysis on the background thread.

        Any unhandled exception emits ``finished([])`` so the UI is never
        left waiting for a signal that never arrives.  Unexpected failures
        are logged at WARNING (not DEBUG) so they are visible without
        enabling verbose logging.
        """
        try:
            findings = self._execute()
        except Exception:
            logger.warning("Intelligence worker: unexpected failure", exc_info=True)
            findings = []
        self.finished.emit(findings)

    # ── Orchestration ─────────────────────────────────────────────────────────

    def _execute(self) -> list[Finding]:
        """Orchestrate the full analysis pipeline.

        Each distinct responsibility is delegated to a focused private method:
        URL resolution, context fetching, engine execution, and result
        persistence.  Interruption is checked between the two most expensive
        phases so the caller can cancel cleanly at either boundary.
        """
        mgr = ResponseIntelligenceManager(self._db)
        url_pattern = self._resolve_url_pattern()
        method = self._request.method.upper()

        ctx = self._fetch_analysis_context(mgr, url_pattern, method)

        # Allow clean cancellation between the (potentially heavy) DB prefetch
        # and the analysis engine.
        if self.isInterruptionRequested():
            logger.debug("Intelligence worker: interrupted before analysis")
            return []

        findings = self._run_engine(ctx)

        # Generate request-level hints from historical similarity and merge
        # them with response analyzers before presenting in the Intelligence panel.
        findings.extend(self._run_recommender_hints())

        # Allow cancellation before the write-back phase so we don't issue
        # unnecessary DB writes for a discarded result.
        if self.isInterruptionRequested():
            logger.debug("Intelligence worker: interrupted before persistence")
            return findings

        self._persist_results(mgr, url_pattern, method)
        return findings

    def _run_recommender_hints(self) -> list[Finding]:
        """Generate recommender hints for the current request.

        This executes inside the worker thread to keep GUI interactions non-blocking.
        """
        if self._RECOMMENDER_ANALYZER_ID in self._disabled:
            logger.debug("Intelligence worker: recommender disabled by settings")
            return []

        try:
            req_payload = {
                "method": self._request.method,
                "url": self._request.url,
                "headers": dict(self._request.headers or {}),
                "params": dict(self._request.params or {}),
            }
            suggestions = Recommender(self._db).generate_suggestions(req_payload)
            if not suggestions:
                return []

            findings = suggestions_to_findings(suggestions)
            logger.debug(
                "Intelligence worker: recommender generated %d findings",
                len(findings),
            )
            return findings
        except Exception:
            logger.exception("Intelligence worker: recommender hints failed", exc_info=True)
            return []

    # ── URL resolution ────────────────────────────────────────────────────────

    def _resolve_url_pattern(self) -> str:
        """Derive and normalise the URL pattern used as the DB lookup key.

        Falls back to ``"/"`` when neither ``sent_url`` nor ``url`` yields a
        non-empty value so downstream callers always receive a valid key.
        """
        raw_url = self._response.sent_url or self._request.url
        if not raw_url:
            logger.debug("Intelligence worker: no URL available; using '/'")
            return "/"
        return normalize_url_pattern(raw_url)

    # ── Context fetching (read phase) ─────────────────────────────────────────

    def _fetch_analysis_context(
        self,
        mgr: ResponseIntelligenceManager,
        url_pattern: str,
        method: str,
    ) -> AnalysisContext:
        """Fetch all historical data needed by the analysis engine.

        Each DB call is wrapped individually via ``_try_fetch`` so a transient
        error in one query degrades gracefully instead of aborting the whole
        context build.
        """
        endpoint_stats = self._try_fetch(
            lambda: mgr.get_endpoint_stats(url_pattern, method),
            "endpoint stats",
        )
        stored_schema = self._try_fetch(
            lambda: mgr.get_schema(url_pattern, method, status_code=self._response.status_code),
            "stored schema",
        )
        history_rows: list[dict[str, Any]] = (
            self._try_fetch(
                lambda: mgr.get_recent_history(limit=50),
                "recent history",
            )
            or []
        )

        return AnalysisContext(
            request=self._request,
            response=self._response,
            history_rows=history_rows,
            endpoint_stats=endpoint_stats,
            stored_schema=stored_schema,
        )

    # ── Analysis engine ───────────────────────────────────────────────────────

    def _run_engine(self, ctx: AnalysisContext) -> list[Finding]:
        """Instantiate and run the analysis engine for this worker's config."""
        engine = AnalysisEngine(disabled=set(self._disabled))
        return engine.analyze(ctx)

    # ── Result persistence (write phase) ──────────────────────────────────────

    def _persist_results(
        self,
        mgr: ResponseIntelligenceManager,
        url_pattern: str,
        method: str,
    ) -> None:
        """Write timing stats and schema snapshots back to the database.

        Each write is isolated so a failure in one does not prevent the other
        from completing.
        """
        self._update_endpoint_stats(mgr, url_pattern, method)
        self._save_schema_snapshot(mgr, url_pattern, method)

    def _update_endpoint_stats(
        self,
        mgr: ResponseIntelligenceManager,
        url_pattern: str,
        method: str,
    ) -> None:
        """Append the current response time to the endpoint's timing history."""
        elapsed_ms = round(self._response.elapsed * 1000, 2)
        self._try_write(
            lambda: mgr.update_endpoint_stats(url_pattern, method, elapsed_ms),
            "endpoint stats",
        )

    def _save_schema_snapshot(
        self,
        mgr: ResponseIntelligenceManager,
        url_pattern: str,
        method: str,
    ) -> None:
        """Capture a schema fingerprint for JSON responses and persist it."""
        if not self._response.is_json:
            return
        self._try_write(
            lambda: mgr.save_schema(
                url_pattern,
                method,
                SchemaDriftAnalyzer.build_schema_fingerprint(self._response.json()),
                status_code=self._response.status_code,
            ),
            "schema snapshot",
        )

    # ── Generic DB operation helpers ──────────────────────────────────────────

    @staticmethod
    def _try_fetch(operation: Callable[[], _T], label: str) -> _T | None:
        """Execute *operation* and return its result; return ``None`` on error.

        Errors are logged at DEBUG because transient DB failures during the
        read phase are non-critical — the analysis engine runs with whatever
        historical data is available.
        """
        try:
            return operation()
        except Exception:
            logger.exception("Failed to fetch %s", label, exc_info=True)
            return None

    @staticmethod
    def _try_write(operation: Callable[[], None], label: str) -> None:
        """Execute *operation*; log and swallow any exception.

        Write failures are non-fatal — the analysis result is still returned
        to the UI.  DEBUG level is appropriate here because the storage layer
        already logs serious failures at WARNING.
        """
        try:
            operation()
        except Exception:
            logger.exception("Failed to write %s", label, exc_info=True)

    # ── Dunder helpers ────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}("
            f"method={self._request.method!r}, "
            f"url={self._request.url!r}, "
            f"disabled={self._disabled!r})"
        )
