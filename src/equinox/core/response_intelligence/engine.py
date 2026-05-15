"""analysis engine with explicit analyzer registry."""

from __future__ import annotations

import inspect
import logging
from importlib import import_module
from typing import Dict, List, Optional, Set

from equinox.core import urls
from equinox.core.response_intelligence.base import Analyzer
from equinox.core.response_intelligence.models import (
    AnalysisContext,
    Category,
    Finding,
    Severity,
)

logger = logging.getLogger(__name__)

_SEVERITY_ORDER = {Severity.CRITICAL: 0, Severity.WARNING: 1, Severity.INFO: 2}

_ANALYZER_MODULES: tuple = (
    # Security analyzers (split from monolithic security.py into focused modules)
    "equinox.core.response_intelligence.analyzers.headers",
    "equinox.core.response_intelligence.analyzers.cookies",
    "equinox.core.response_intelligence.analyzers.cors",
    "equinox.core.response_intelligence.analyzers.jwt",
    "equinox.core.response_intelligence.analyzers.pii_secret_leak",
    "equinox.core.response_intelligence.analyzers.sensitive_data",
    # Other analyzer categories
    "equinox.core.response_intelligence.performance",
    "equinox.core.response_intelligence.consistency",
    "equinox.core.response_intelligence.server",
    "equinox.core.response_intelligence.hints",
)


def normalize_url_pattern(url: str) -> str:
    parts = urls.normalized_parts(url)
    segments = parts.get("path_segments") or []
    return "/" + "/".join(segments) if segments else "/"


class AnalysisEngine:
    """Discovers analyzers and executes them."""

    def __init__(self, disabled: Optional[Set[str]] = None) -> None:
        self._disabled = disabled or set()
        self._analyzers: List[Analyzer] = []
        self._discovered_cache: Optional[List[Analyzer]] = None

    def _get_discovered_analyzers(self) -> List[Analyzer]:
        if self._discovered_cache is None:
            self._discovered_cache = self.discover_analyzers()
        cache = self._discovered_cache or []
        return list(cache)

    @classmethod
    def discover_analyzers(cls) -> List[Analyzer]:
        analyzers: List[Analyzer] = []
        for module_name in _ANALYZER_MODULES:
            try:
                module = import_module(module_name)
            except Exception:
                logger.error("Failed to import analyzer module %s", module_name, exc_info=True)
                continue

            for _, obj in inspect.getmembers(module, inspect.isclass):
                if issubclass(obj, Analyzer) and obj is not Analyzer and obj.__module__ == module_name:
                    try:
                        analyzers.append(obj())
                    except Exception:
                        logger.error("Failed to instantiate analyzer %s", obj.__name__, exc_info=True)

        return analyzers

    def load_analyzers(self) -> None:
        discovered = [
            analyzer
            for analyzer in self._get_discovered_analyzers()
            if analyzer.analyzer_id not in self._disabled
        ]

        unique: Dict[str, Analyzer] = {}
        for analyzer in discovered:
            analyzer_id = (analyzer.analyzer_id or "").strip()
            if not analyzer_id:
                logger.warning("Skipping analyzer with empty analyzer_id: %s", type(analyzer).__name__)
                continue
            if analyzer_id in unique:
                logger.warning("Duplicate analyzer_id %r ignored (%s)", analyzer_id, type(analyzer).__name__)
                continue
            unique[analyzer_id] = analyzer

        self._analyzers = [unique[key] for key in sorted(unique.keys())]

    def get_all_analyzer_info(self) -> List[Dict[str, str]]:
        info = [
            {
                "id": analyzer.analyzer_id,
                "name": analyzer.display_name or analyzer.analyzer_id,
                "category": analyzer.category.value,
            }
            for analyzer in self._get_discovered_analyzers()
            if (analyzer.analyzer_id or "").strip()
        ]
        info.sort(key=lambda item: item["id"])
        return info

    @staticmethod
    def _failure_finding(analyzer_id: str, reason: str) -> Finding:
        safe_id = analyzer_id or "unknown"
        return Finding(
            category=Category.HINTS,
            severity=Severity.WARNING,
            title=f"Analyzer failed: {safe_id}",
            description=reason,
            analyzer_id="hints.analysis_failure",
            recommendation="Review analyzer implementation and logs to restore complete analysis coverage.",
            details={"failed_analyzer": safe_id, "reason": reason},
        )

    def analyze(self, ctx: AnalysisContext) -> List[Finding]:
        if not self._analyzers:
            self.load_analyzers()

        has_valid_json_body = self._has_valid_json_body(ctx)
        findings: List[Finding] = []
        for analyzer in self._analyzers:
            if analyzer.requires_valid_json_body and not has_valid_json_body:
                logger.debug(
                    "Skipping analyzer %s due to invalid JSON response body",
                    analyzer.analyzer_id,
                )
                continue
            try:
                results = analyzer.analyze(ctx)
                if not isinstance(results, list):
                    logger.warning(
                        "Analyzer %s returned non-list result (%s)",
                        analyzer.analyzer_id,
                        type(results).__name__,
                    )
                    findings.append(self._failure_finding(
                        analyzer.analyzer_id,
                        f"Analyzer returned invalid result type: {type(results).__name__}",
                    ))
                    continue
                findings.extend(results)
            except Exception as exc:
                logger.warning("Analyzer %s raised and was skipped", analyzer.analyzer_id, exc_info=True)
                findings.append(self._failure_finding(
                    analyzer.analyzer_id,
                    f"Analyzer raised {type(exc).__name__}",
                ))

        findings.sort(
            key=lambda finding: (
                _SEVERITY_ORDER.get(finding.severity, 99),
                finding.category.value,
                finding.title.lower(),
                finding.analyzer_id,
            )
        )
        return findings

    @staticmethod
    def _has_valid_json_body(ctx: AnalysisContext) -> bool:
        response = ctx.response
        if not response.body or not response.is_json:
            return False

        try:
            response.json()
        except Exception:
            return False
        return True

