"""Analysis engine — discovers and runs all analyzers."""

import logging
import re
from typing import Dict, List, Optional, Set

from equinox.core.response_intelligence.base import Analyzer
from equinox.core.response_intelligence.models import (
    AnalysisContext,
    Finding,
    Severity,
)

logger = logging.getLogger(__name__)

# Priority for sorting: critical first
_SEVERITY_ORDER = {Severity.CRITICAL: 0, Severity.WARNING: 1, Severity.INFO: 2}

# Regex used to normalise URL paths for endpoint grouping.
_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)
_NUMERIC_SEGMENT_RE = re.compile(r"/\d+(?=/|$)")


def normalize_url_pattern(url: str) -> str:
    """Collapse numeric/UUID path segments so the same endpoint groups together.

    ``/users/123/posts/456`` → ``/users/{id}/posts/{id}``
    Query string is stripped.
    """
    # Strip query/fragment
    path = url.split("?", 1)[0].split("#", 1)[0]
    # Strip scheme + authority (keep path only)
    if "://" in path:
        path = "/" + path.split("://", 1)[1].split("/", 1)[-1]
    path = _UUID_RE.sub("{id}", path)
    path = _NUMERIC_SEGMENT_RE.sub("/{id}", path)
    return path


class AnalysisEngine:
    """Discovers built-in analyzers and runs them against a context."""

    def __init__(self, disabled: Optional[Set[str]] = None) -> None:
        self._disabled = disabled or set()
        self._analyzers: List[Analyzer] = []

    # ------------------------------------------------------------------
    # Registry
    # ------------------------------------------------------------------

    @classmethod
    def discover_analyzers(cls) -> List[Analyzer]:
        """Instantiate all built-in analyzers."""
        from equinox.core.response_intelligence.security import (
            MissingSecurityHeadersAnalyzer,
            CookieFlagsAnalyzer,
            PIILeakDetectionAnalyzer,
            CORSMisconfigAnalyzer,
            JWTDecodeAnalyzer,
        )
        from equinox.core.response_intelligence.performance import (
            CompressionAnalyzer,
            TimingBreakdownAnalyzer,
            ResponseTimePercentileAnalyzer,
            PaginationDetectionAnalyzer,
        )
        from equinox.core.response_intelligence.consistency import (
            StatusBodyMismatchAnalyzer,
            ContentTypeMismatchAnalyzer,
            DuplicateJsonKeysAnalyzer,
            DateFormatInconsistencyAnalyzer,
            NullVsMissingAnalyzer,
            SchemaDriftAnalyzer,
        )
        from equinox.core.response_intelligence.server import (
            ServerFingerprintAnalyzer,
            RateLimitDashboardAnalyzer,
            CachingBehaviorAnalyzer,
            APIVersionDetectionAnalyzer,
            ResponseTimeAnomalyAnalyzer,
        )
        from equinox.core.response_intelligence.hints import (
            DeprecatedAPIAnalyzer,
            SuggestedEncodingAnalyzer,
            NPlusOneDetectionAnalyzer,
            ResponseEncodingIssuesAnalyzer,
            LinkHeaderParsingAnalyzer,
        )

        return [
            # Security
            MissingSecurityHeadersAnalyzer(),
            CookieFlagsAnalyzer(),
            PIILeakDetectionAnalyzer(),
            CORSMisconfigAnalyzer(),
            JWTDecodeAnalyzer(),
            # Performance
            CompressionAnalyzer(),
            TimingBreakdownAnalyzer(),
            ResponseTimePercentileAnalyzer(),
            PaginationDetectionAnalyzer(),
            # Consistency
            StatusBodyMismatchAnalyzer(),
            ContentTypeMismatchAnalyzer(),
            DuplicateJsonKeysAnalyzer(),
            DateFormatInconsistencyAnalyzer(),
            NullVsMissingAnalyzer(),
            SchemaDriftAnalyzer(),
            # Server
            ServerFingerprintAnalyzer(),
            RateLimitDashboardAnalyzer(),
            CachingBehaviorAnalyzer(),
            APIVersionDetectionAnalyzer(),
            ResponseTimeAnomalyAnalyzer(),
            # Hints
            DeprecatedAPIAnalyzer(),
            SuggestedEncodingAnalyzer(),
            NPlusOneDetectionAnalyzer(),
            ResponseEncodingIssuesAnalyzer(),
            LinkHeaderParsingAnalyzer(),
        ]

    def load_analyzers(self) -> None:
        """Populate ``self._analyzers`` from the built-in registry."""
        self._analyzers = [
            a for a in self.discover_analyzers()
            if a.analyzer_id not in self._disabled
        ]

    def get_all_analyzer_info(self) -> List[Dict[str, str]]:
        """Return metadata for every known analyzer (for preferences UI)."""
        return [
            {
                "id": a.analyzer_id,
                "name": a.display_name or a.analyzer_id,
                "category": a.category.value,
            }
            for a in self.discover_analyzers()
        ]

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def analyze(self, ctx: AnalysisContext) -> List[Finding]:
        """Run all enabled analyzers and return findings sorted by severity."""
        if not self._analyzers:
            self.load_analyzers()

        findings: List[Finding] = []
        for analyzer in self._analyzers:
            try:
                results = analyzer.analyze(ctx)
                findings.extend(results)
            except Exception:
                logger.debug(
                    "Analyzer %s raised an exception — skipped",
                    analyzer.analyzer_id,
                    exc_info=True,
                )
        findings.sort(key=lambda f: _SEVERITY_ORDER.get(f.severity, 99))
        return findings

