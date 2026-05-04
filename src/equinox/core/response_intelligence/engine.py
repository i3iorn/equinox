"""Analysis engine — discovers and runs all analyzers."""

import logging
import pkgutil
import importlib
import inspect
from typing import Dict, List, Optional, Set
from equinox.core import urls

from equinox.core.response_intelligence.base import Analyzer
from equinox.core.response_intelligence.models import (
    AnalysisContext,
    Finding,
    Severity,
)

logger = logging.getLogger(__name__)

# Priority for sorting: critical first
_SEVERITY_ORDER = {Severity.CRITICAL: 0, Severity.WARNING: 1, Severity.INFO: 2}

def normalize_url_pattern(url: str) -> str:
    """Return a path-only normalized pattern for *url*.

    Delegates to `equinox.core.urls.normalized_parts()` and builds a
    path like ``/users/{id}/posts/{id}``. Query string and scheme/netloc
    are stripped.
    """
    parts = urls.normalized_parts(url)
    segs = parts.get("path_segments") or []
    return "/" + "/".join(segs) if segs else "/"


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
        """Instantiate all built-in analyzers using dynamic discovery."""
        import equinox.core.response_intelligence as ri_pkg

        analyzers: List[Analyzer] = []
        # Find all modules in the response_intelligence package
        for _, name, is_pkg in pkgutil.iter_modules(ri_pkg.__path__, ri_pkg.__name__ + "."):
            if is_pkg or name.endswith(".base") or name.endswith(".models") or name.endswith(".engine"):
                continue

            try:
                module = importlib.import_module(name)
                for _, obj in inspect.getmembers(module, inspect.isclass):
                    # Check if it's a subclass of Analyzer but not the Analyzer class itself
                    if issubclass(obj, Analyzer) and obj is not Analyzer:
                        try:
                            analyzers.append(obj())
                        except Exception:
                            logger.error("Failed to instantiate analyzer %s", obj, exc_info=True)
            except Exception:
                logger.error("Failed to load module %s for analyzer discovery", name, exc_info=True)

        return analyzers

    def load_analyzers(self) -> None:
        """Populate ``self._analyzers`` from the built-in registry."""
        discovered = [
            a for a in self.discover_analyzers()
            if a.analyzer_id not in self._disabled
        ]

        unique: Dict[str, Analyzer] = {}
        for analyzer in discovered:
            analyzer_id = (analyzer.analyzer_id or "").strip()
            if not analyzer_id:
                logger.warning(
                    "Skipping analyzer with empty analyzer_id: %s",
                    type(analyzer).__name__,
                )
                continue
            if analyzer_id in unique:
                logger.warning(
                    "Duplicate analyzer_id %r ignored (%s)",
                    analyzer_id,
                    type(analyzer).__name__,
                )
                continue
            unique[analyzer_id] = analyzer

        self._analyzers = [unique[k] for k in sorted(unique.keys())]

    def get_all_analyzer_info(self) -> List[Dict[str, str]]:
        """Return metadata for every known analyzer (for preferences UI)."""
        info = [
            {
                "id": a.analyzer_id,
                "name": a.display_name or a.analyzer_id,
                "category": a.category.value,
            }
            for a in self.discover_analyzers()
            if (a.analyzer_id or "").strip()
        ]
        info.sort(key=lambda i: i["id"])
        return info

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
                if not isinstance(results, list):
                    logger.warning(
                        "Analyzer %s returned non-list result (%s)",
                        analyzer.analyzer_id,
                        type(results).__name__,
                    )
                    continue
                findings.extend(results)
            except Exception:
                logger.debug(
                    "Analyzer %s raised an exception — skipped",
                    analyzer.analyzer_id,
                    exc_info=True,
                )
        findings.sort(
            key=lambda f: (
                _SEVERITY_ORDER.get(f.severity, 99),
                f.category.value,
                f.title.lower(),
                f.analyzer_id,
            )
        )
        return findings

