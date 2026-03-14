"""Background worker for Response Intelligence analysis."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set

from PyQt6.QtCore import QThread, pyqtSignal

from equinox.core.request import Request, Response
from equinox.core.response_intelligence.engine import AnalysisEngine, normalize_url_pattern
from equinox.core.response_intelligence.models import AnalysisContext, Finding
from equinox.core.response_intelligence.consistency import SchemaDriftAnalyzer

logger = logging.getLogger(__name__)


class IntelligenceWorker(QThread):
    """Run response intelligence analysis on a background thread.

    Emits ``finished(list)`` with ``List[Finding]`` when done.
    After analysis, updates endpoint stats and schema snapshots in the DB.
    """

    finished = pyqtSignal(list)  # List[Finding]

    def __init__(
        self,
        request: Request,
        response: Response,
        db: Any,  # equinox.storage.database.Database
        disabled_analyzers: Optional[Set[str]] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._request = request
        self._response = response
        self._db = db
        self._disabled = disabled_analyzers or set()

    def run(self) -> None:
        try:
            findings = self._execute()
            self.finished.emit(findings)
        except Exception:
            logger.debug("Intelligence worker failed", exc_info=True)
            self.finished.emit([])

    def _execute(self) -> List[Finding]:
        from equinox.storage.response_intelligence import ResponseIntelligenceManager

        mgr = ResponseIntelligenceManager(self._db)
        url_pattern = normalize_url_pattern(self._response.sent_url or self._request.url)
        method = self._request.method.upper()

        # Gather historical context
        endpoint_stats = None
        stored_schema = None
        history_rows: List[Dict[str, Any]] = []

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

        # Build context and run engine
        ctx = AnalysisContext(
            request=self._request,
            response=self._response,
            history_rows=history_rows,
            endpoint_stats=endpoint_stats,
            stored_schema=stored_schema,
        )

        engine = AnalysisEngine(disabled=self._disabled)
        findings = engine.analyze(ctx)

        # Post-analysis: update stats and schema
        try:
            elapsed_ms = self._response.elapsed * 1000
            mgr.update_endpoint_stats(url_pattern, method, round(elapsed_ms, 2))
        except Exception:
            logger.debug("Failed to update endpoint stats", exc_info=True)

        try:
            if self._response.is_json:
                obj = self._response.json()
                schema = SchemaDriftAnalyzer.build_schema_fingerprint(obj)
                mgr.save_schema(url_pattern, method, schema)
        except Exception:
            logger.debug("Failed to save schema snapshot", exc_info=True)

        return findings

