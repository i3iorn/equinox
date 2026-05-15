"""Sensitive data caching analyzer."""

import logging
from typing import Any, Dict, List, Set

from equinox.core.response_intelligence.base import Analyzer
from equinox.core.response_intelligence.models import (
    AnalysisContext,
    Category,
    Finding,
    Severity,
)
from equinox.core.response_intelligence.analyzers.pii_secret_leak import (
    _contains_sensitive_values,
    _SENSITIVE_VALUE_PATTERNS,
)

logger = logging.getLogger(__name__)


def _contains_sensitive_keys(value: Any, sensitive_keys: Set[str], depth: int = 0) -> bool:
    """Recursively check if value contains sensitive keys."""
    if depth > 6:
        return False

    if isinstance(value, dict):
        for key, nested in value.items():
            if str(key).lower() in sensitive_keys:
                return True
            if _contains_sensitive_keys(nested, sensitive_keys, depth + 1):
                return True
    elif isinstance(value, list):
        for nested in value[:50]:
            if _contains_sensitive_keys(nested, sensitive_keys, depth + 1):
                return True
    return False


class SensitiveDataCachingAnalyzer(Analyzer):
    """Detects cacheable responses containing sensitive data."""

    analyzer_id = "security.sensitive_cache"
    category = Category.SECURITY
    display_name = "Sensitive Data Caching"

    _SENSITIVE_KEYS = {
        "access_token",
        "refresh_token",
        "id_token",
        "token",
        "password",
        "passwd",
        "secret",
        "api_key",
        "apikey",
        "authorization",
        "ssn",
        "card_number",
        "credit_card",
    }

    def analyze(self, ctx: AnalysisContext) -> List[Finding]:
        """Analyze Cache-Control headers for sensitive response data."""
        findings: List[Finding] = []
        cache_control = (ctx.response.headers.get("cache-control", "") or "").lower()
        pragma = (ctx.response.headers.get("pragma", "") or "").lower()

        exposure_signals: List[str] = []
        if ctx.response.headers.get("set-cookie"):
            exposure_signals.append("set-cookie")
        if ctx.response.headers.get("authorization"):
            exposure_signals.append("authorization header")

        parsed_json = ctx.response.json_safe()
        if _contains_sensitive_keys(parsed_json, self._SENSITIVE_KEYS):
            exposure_signals.append("sensitive fields in body")
        if _contains_sensitive_values(ctx.response.text[:256_000], _SENSITIVE_VALUE_PATTERNS):
            exposure_signals.append("sensitive value patterns in body")

        if not exposure_signals:
            return findings

        issues: List[str] = []
        severity = Severity.WARNING
        if "public" in cache_control:
            issues.append("Cache-Control includes 'public' for a response that appears to include sensitive data.")
            severity = Severity.CRITICAL
        if "no-store" not in cache_control:
            issues.append("Cache-Control does not include 'no-store' for sensitive response data.")
        if "no-cache" in pragma and "no-store" not in cache_control:
            issues.append("Pragma: no-cache is set but Cache-Control: no-store is missing.")

        if not issues:
            return findings

        findings.append(Finding(
            category=self.category,
            severity=severity,
            title="Sensitive response may be cacheable",
            description="\n".join(issues),
            analyzer_id=self.analyzer_id,
            recommendation="For sensitive responses, return Cache-Control: no-store (and avoid public caches).",
            details={
                "signals": exposure_signals,
                "cache_control": cache_control,
                "pragma": pragma,
            },
        ))
        return findings

