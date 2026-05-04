"""Server Intelligence analyzers."""

from __future__ import annotations

import json
import logging
import math
import re
import time
from typing import Any, Dict, List, Optional

from equinox.core.response_intelligence.base import Analyzer
from equinox.core.response_intelligence.models import (
    AnalysisContext,
    Category,
    Finding,
    Severity,
)

logger = logging.getLogger(__name__)


def _coerce_numeric_samples(values: object, max_samples: int = 500) -> List[float]:
    """Return finite numeric samples from possibly malformed stored values."""
    if not isinstance(values, list):
        return []

    numeric: List[float] = []
    for item in values:
        try:
            value = float(item)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            numeric.append(value)

    if len(numeric) > max_samples:
        return numeric[-max_samples:]
    return numeric


class ServerFingerprintAnalyzer(Analyzer):
    analyzer_id = "server.fingerprint"
    category = Category.SERVER
    display_name = "Server Technology Fingerprint"

    # header → label
    _FINGERPRINT_HEADERS = {
        "server": "Server",
        "x-powered-by": "Powered By",
        "via": "Via / Proxy",
        "x-aspnet-version": "ASP.NET Version",
        "x-aspnetmvc-version": "ASP.NET MVC",
        "x-generator": "Generator",
        "x-drupal-cache": "Drupal",
        "x-varnish": "Varnish",
        "x-cache": "Cache Status",
        "cf-ray": "Cloudflare Ray ID",
        "fly-request-id": "Fly.io",
        "x-vercel-id": "Vercel",
        "x-amzn-requestid": "AWS",
        "x-request-id": "Request ID",
    }

    def analyze(self, ctx: AnalysisContext) -> List[Finding]:
        findings: List[Finding] = []
        hdrs = ctx.response.headers
        detected: Dict[str, str] = {}
        for hdr, label in self._FINGERPRINT_HEADERS.items():
            val = hdrs.get(hdr, "")
            if val:
                detected[label] = val

        if not detected:
            return findings

        parts = [f"{label}: {val}" for label, val in detected.items()]
        findings.append(Finding(
            category=self.category,
            severity=Severity.INFO,
            title=f"Server stack: {detected.get('Server', next(iter(detected.values())))}",
            description=" · ".join(parts[:6]),
            analyzer_id=self.analyzer_id,
            recommendation="Avoid exposing detailed server fingerprint headers in production unless required.",
            details=detected,
        ))
        return findings


class RateLimitDashboardAnalyzer(Analyzer):
    analyzer_id = "server.rate_limit"
    category = Category.SERVER
    display_name = "Rate Limit Dashboard"

    _LIMIT_KEYS = ("x-ratelimit-limit", "x-rate-limit-limit", "ratelimit-limit")
    _REMAINING_KEYS = ("x-ratelimit-remaining", "x-rate-limit-remaining", "ratelimit-remaining")
    _RESET_KEYS = ("x-ratelimit-reset", "x-rate-limit-reset", "ratelimit-reset")

    def analyze(self, ctx: AnalysisContext) -> List[Finding]:
        findings: List[Finding] = []
        hdrs = ctx.response.headers

        limit = self._first(hdrs, self._LIMIT_KEYS)
        remaining = self._first(hdrs, self._REMAINING_KEYS)
        reset_raw = self._first(hdrs, self._RESET_KEYS)
        retry_after = hdrs.get("retry-after", "")

        if limit is None and remaining is None and not retry_after:
            return findings

        detail: Dict[str, Any] = {}
        parts: List[str] = []

        if limit is not None:
            detail["limit"] = limit
            parts.append(f"Limit: {limit}")
        if remaining is not None:
            detail["remaining"] = remaining
            parts.append(f"Remaining: {remaining}")

            # Usage ratio warning
            if limit is not None:
                try:
                    ratio = 1 - int(remaining) / int(limit)
                    detail["usage_pct"] = round(ratio * 100, 1)
                except (ValueError, ZeroDivisionError):
                    pass

        if reset_raw is not None:
            detail["reset_raw"] = reset_raw
            try:
                reset_ts = int(reset_raw)
                if reset_ts > 1_000_000_000:
                    secs = reset_ts - int(time.time())
                    if secs > 0:
                        detail["resets_in_seconds"] = secs
                        parts.append(f"Resets in {secs}s")
                else:
                    parts.append(f"Resets in {reset_ts}s")
            except ValueError:
                parts.append(f"Reset: {reset_raw}")

        if retry_after:
            detail["retry_after"] = retry_after
            parts.append(f"Retry-After: {retry_after}")

        sev = Severity.INFO
        usage = detail.get("usage_pct", 0)
        if usage >= 90:
            sev = Severity.WARNING
        if ctx.response.status_code == 429:
            sev = Severity.CRITICAL
            logger.warning(
                "RateLimitDashboardAnalyzer: 429 Too Many Requests — limit=%s remaining=%s",
                limit, remaining,
            )

        findings.append(Finding(
            category=self.category,
            severity=sev,
            title="Rate limit status" + (f" ({usage:.0f}% used)" if usage else ""),
            description=" · ".join(parts) if parts else "Rate limit headers detected.",
            analyzer_id=self.analyzer_id,
            recommendation="Back off or retry later when near limit, and consider client-side throttling.",
            details=detail,
        ))
        return findings

    @staticmethod
    def _first(hdrs: dict, keys: tuple) -> "Optional[str]":
        for k in keys:
            v = hdrs.get(k)
            if v is not None:
                return v
        return None


class CachingBehaviorAnalyzer(Analyzer):
    analyzer_id = "server.caching"
    category = Category.SERVER
    display_name = "Caching Behaviour Summary"

    def analyze(self, ctx: AnalysisContext) -> List[Finding]:
        findings: List[Finding] = []
        hdrs = ctx.response.headers

        cc = hdrs.get("cache-control", "")
        etag = hdrs.get("etag", "")
        expires = hdrs.get("expires", "")
        vary = hdrs.get("vary", "")
        age = hdrs.get("age", "")
        pragma = hdrs.get("pragma", "")

        if not any((cc, etag, expires, vary, age, pragma)):
            return findings

        parts: List[str] = []
        detail: Dict[str, str] = {}

        if cc:
            detail["cache-control"] = cc
            parts.append(self._explain_cache_control(cc))
        if etag:
            detail["etag"] = etag
            parts.append("Has ETag (conditional requests supported)")
        if expires:
            detail["expires"] = expires
            parts.append(f"Expires: {expires}")
        if vary:
            detail["vary"] = vary
            parts.append(f"Vary: {vary}")
        if age:
            detail["age"] = age
            parts.append(f"Age: {age}s (served from cache)")
        if pragma:
            detail["pragma"] = pragma

        findings.append(Finding(
            category=self.category,
            severity=Severity.INFO,
            title="Caching: " + (self._short_summary(cc) if cc else "headers present"),
            description=" · ".join(parts),
            analyzer_id=self.analyzer_id,
            recommendation="Set explicit Cache-Control directives and validators (ETag/Last-Modified) for predictable caching.",
            details=detail,
        ))
        return findings

    @staticmethod
    def _explain_cache_control(cc: str) -> str:
        directives = [d.strip() for d in cc.split(",")]
        explanations: List[str] = []
        for d in directives:
            low = d.lower()
            if low == "no-store":
                explanations.append("no-store (never cache)")
            elif low == "no-cache":
                explanations.append("no-cache (must revalidate)")
            elif low == "public":
                explanations.append("public (CDN-cacheable)")
            elif low == "private":
                explanations.append("private (browser only)")
            elif low.startswith("max-age="):
                try:
                    secs = int(low.split("=")[1])
                    if secs >= 86400:
                        explanations.append(f"max-age={secs} ({secs // 86400}d)")
                    elif secs >= 3600:
                        explanations.append(f"max-age={secs} ({secs // 3600}h)")
                    else:
                        explanations.append(f"max-age={secs}s")
                except ValueError:
                    explanations.append(d)
            elif low.startswith("s-maxage="):
                explanations.append(d)
            elif low == "must-revalidate":
                explanations.append("must-revalidate")
            elif low == "immutable":
                explanations.append("immutable (won't change)")
        return "Cache-Control: " + ", ".join(explanations) if explanations else f"Cache-Control: {cc}"

    @staticmethod
    def _short_summary(cc: str) -> str:
        low = cc.lower()
        if "no-store" in low:
            return "no-store"
        if "no-cache" in low:
            return "revalidate"
        if "max-age=" in low:
            m = re.search(r"max-age=(\d+)", low)
            if m:
                secs = int(m.group(1))
                if secs >= 86400:
                    return f"cached {secs // 86400}d"
                if secs >= 3600:
                    return f"cached {secs // 3600}h"
                return f"cached {secs}s"
        return "present"


class APIVersionDetectionAnalyzer(Analyzer):
    analyzer_id = "server.api_version"
    category = Category.SERVER
    display_name = "API Version Detection"

    _URL_VERSION_RE = re.compile(r"/v(\d+(?:\.\d+)?)(?:/|$|\?)")
    _HEADER_KEYS = ("api-version", "x-api-version", "x-version")

    def analyze(self, ctx: AnalysisContext) -> List[Finding]:
        findings: List[Finding] = []
        versions_found: Dict[str, str] = {}

        # URL path
        url = ctx.response.sent_url or ctx.request.url
        m = self._URL_VERSION_RE.search(url)
        if m:
            versions_found["URL path"] = f"v{m.group(1)}"

        # Accept header
        accept = (ctx.request.headers or {}).get("Accept", "")
        if "version=" in accept:
            am = re.search(r"version=([^\s;,]+)", accept)
            if am:
                versions_found["Accept header"] = am.group(1)

        # Response headers
        for hk in self._HEADER_KEYS:
            val = ctx.response.headers.get(hk, "")
            if val:
                versions_found[f"Header ({hk})"] = val

        if not versions_found:
            return findings

        parts = [f"{src}: {ver}" for src, ver in versions_found.items()]
        findings.append(Finding(
            category=self.category,
            severity=Severity.INFO,
            title=f"API version: {next(iter(versions_found.values()))}",
            description=" · ".join(parts),
            analyzer_id=self.analyzer_id,
            recommendation="Use one canonical API versioning strategy (path, header, or media type) across endpoints.",
            details=versions_found,
        ))
        return findings


class ResponseTimeAnomalyAnalyzer(Analyzer):
    analyzer_id = "server.time_anomaly"
    category = Category.SERVER
    display_name = "Response Time Anomaly"

    _ZSCORE_THRESHOLD = 2.5

    def analyze(self, ctx: AnalysisContext) -> List[Finding]:
        findings: List[Finding] = []
        stats = ctx.endpoint_stats
        if not stats:
            return findings

        elapsed_values_raw = stats.get("elapsed_values", "[]")
        try:
            values = json.loads(elapsed_values_raw) if isinstance(elapsed_values_raw, str) else elapsed_values_raw
        except Exception:
            return findings

        values = _coerce_numeric_samples(values)
        if not values:
            logger.debug("ResponseTimeAnomalyAnalyzer: no valid numeric samples")
            return findings

        if len(values) < 5:
            return findings

        import statistics as _stats
        mean = _stats.mean(values)
        stdev = _stats.stdev(values)
        if stdev == 0:
            return findings

        current_ms = ctx.response.elapsed * 1000
        zscore = (current_ms - mean) / stdev

        if abs(zscore) < self._ZSCORE_THRESHOLD:
            return findings

        detail = {
            "current_ms": round(current_ms, 1),
            "mean_ms": round(mean, 1),
            "stdev_ms": round(stdev, 1),
            "zscore": round(zscore, 2),
            "sample_size": len(values),
        }

        if zscore > 0:
            logger.debug(
                "ResponseTimeAnomalyAnalyzer: slow response %dms (z=%.2f, mean=%dms)",
                round(current_ms), zscore, round(mean),
            )
            findings.append(Finding(
                category=self.category,
                severity=Severity.WARNING,
                title=f"Response {round(current_ms)}ms is abnormally slow (z={zscore:.1f})",
                description=f"Average for this endpoint: {round(mean)}ms ± {round(stdev)}ms. "
                            f"Current response is {zscore:.1f} standard deviations above average.",
                analyzer_id=self.analyzer_id,
                recommendation="Inspect recent backend dependencies and slow queries for this endpoint.",
                details=detail,
            ))
        else:
            findings.append(Finding(
                category=self.category,
                severity=Severity.INFO,
                title=f"Response {round(current_ms)}ms is unusually fast (z={zscore:.1f})",
                description=f"Average for this endpoint: {round(mean)}ms ± {round(stdev)}ms.",
                analyzer_id=self.analyzer_id,
                recommendation="Track whether the improvement is sustained and capture what changed.",
                details=detail,
            ))
        return findings

