"""Background worker for Response Intelligence analysis."""
from __future__ import annotations

import logging

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from equinox.core.request import Request, Response
from equinox.storage import Database
from equinox.core.response_intelligence.consistency import SchemaDriftAnalyzer
from equinox.core.response_intelligence.engine import AnalysisEngine, normalize_url_pattern
from equinox.core.response_intelligence.models import AnalysisContext, Finding
from equinox.storage.response_intelligence import ResponseIntelligenceManager

__all__ = ["IntelligenceWorker"]

logger = logging.getLogger(__name__)


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
            construction.
        parent: Optional Qt parent object.
    """

    finished = pyqtSignal(list)  # list[Finding]

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
        # Store as frozenset — immutable and hashable; callers cannot mutate
        # the disabled set after the worker has been constructed.
        self._disabled: frozenset[str] = (
            frozenset(disabled_analyzers) if disabled_analyzers else frozenset()
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

    # ── Internal ──────────────────────────────────────────────────────────────

    def _execute(self) -> list[Finding]:
        """Run the full analysis pipeline.

        Each database step is wrapped individually so a transient DB error
        degrades gracefully rather than aborting the entire analysis.

        An interruption check between the DB prefetch phase and the analysis
        engine call allows the caller to call ``requestInterruption()`` and
        have the worker exit cleanly without running the expensive engine.
        """
        mgr = ResponseIntelligenceManager(self._db)
        url_pattern = normalize_url_pattern(
            self._response.sent_url or self._request.url
        )
        method = self._request.method.upper()

        # ── Gather historical context ─────────────────────────────────
        endpoint_stats = None
        stored_schema = None
        history_rows: list[dict] = []

        try:
            endpoint_stats = mgr.get_endpoint_stats(url_pattern, method)
        except Exception:
            logger.debug("Failed to fetch endpoint stats", exc_info=True)

        try:
            stored_schema = mgr.get_schema(url_pattern, method)
        except Exception:
            logger.debug("Failed to fetch stored schema", exc_info=True)

        try:
            history_rows = mgr.get_recent_history(limit=50)
        except Exception:
            logger.debug("Failed to fetch recent history", exc_info=True)

        # Bail out cleanly if the caller cancelled us before the (expensive)
        # analysis engine step.
        if self.isInterruptionRequested():
            logger.debug("Intelligence worker: interrupted before analysis")
            return []

        # ── Run analysis engine ───────────────────────────────────────
        ctx = AnalysisContext(
            request=self._request,
            response=self._response,
            history_rows=history_rows,
            endpoint_stats=endpoint_stats,
            stored_schema=stored_schema,
        )
        engine = AnalysisEngine(disabled=set(self._disabled) or None)
        findings = engine.analyze(ctx)

        # ── Post-analysis: update stats and schema ────────────────────
        elapsed = self._response.elapsed
        if elapsed is not None:
            try:
                mgr.update_endpoint_stats(url_pattern, method, round(elapsed * 1000, 2))
            except Exception:
                logger.debug("Failed to update endpoint stats", exc_info=True)
        else:
            logger.debug(
                "Skipping endpoint stats update: elapsed is None for %s %s",
                method,
                url_pattern,
            )

        try:
            if self._response.is_json:
                obj = self._response.json()
                schema = SchemaDriftAnalyzer.build_schema_fingerprint(obj)
                mgr.save_schema(url_pattern, method, schema)
        except Exception:
            logger.debug("Failed to save schema snapshot", exc_info=True)

        return findings

    # ── Dunder helpers ────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}("
            f"method={self._request.method!r}, "
            f"url={self._request.url!r}, "
            f"disabled={self._disabled!r})"
        )
