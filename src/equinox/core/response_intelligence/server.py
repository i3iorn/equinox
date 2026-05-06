"""V2 Server Intelligence analyzers."""

from __future__ import annotations

import json
import logging
import re
import statistics
import time
from typing import Any, Dict, List

from equinox.core.response_intelligence.base import Analyzer
from equinox.core.response_intelligence.models import (
    AnalysisContext,
    Category,
    Finding,
    Severity,
)
from equinox.core.response_intelligence.shared.http import (
    first_present_header,
    parse_cache_control,
    summarize_cache_control,
)
from equinox.core.response_intelligence.shared.stats import coerce_numeric_samples

logger = logging.getLogger(__name__)


class ServerFingerprintAnalyzer(Analyzer):
    analyzer_id = "server.fingerprint"
    category = Category.SERVER
    display_name = "Server Technology Fingerprint"

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
        detected: Dict[str, str] = {}
        for header, label in self._FINGERPRINT_HEADERS.items():
            value = ctx.response.headers.get(header, "")
            if value:
                detected[label] = value

        if not detected:
            return findings

        findings.append(Finding(
            category=self.category,
            severity=Severity.INFO,
            title=f"Server stack: {detected.get('Server', next(iter(detected.values())))}",
            description=" | ".join([f"{k}: {v}" for k, v in list(detected.items())[:6]]),
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
        headers = ctx.response.headers

        limit = first_present_header(headers, self._LIMIT_KEYS)
        remaining = first_present_header(headers, self._REMAINING_KEYS)
        reset_raw = first_present_header(headers, self._RESET_KEYS)
        retry_after = headers.get("retry-after", "")

        if limit is None and remaining is None and not retry_after:
            return findings

        details: Dict[str, Any] = {}
        parts: List[str] = []

        if limit is not None:
            details["limit"] = limit
            parts.append(f"Limit: {limit}")

        if remaining is not None:
            details["remaining"] = remaining
            parts.append(f"Remaining: {remaining}")
            if limit is not None:
                try:
                    usage = 1 - int(remaining) / int(limit)
                    details["usage_pct"] = round(usage * 100, 1)
                except (ValueError, ZeroDivisionError):
                    pass

        if reset_raw is not None:
            details["reset_raw"] = reset_raw
            try:
                reset_num = int(reset_raw)
                if reset_num > 1_000_000_000:
                    secs = reset_num - int(time.time())
                    if secs > 0:
                        details["resets_in_seconds"] = secs
                        parts.append(f"Resets in {secs}s")
                else:
                    parts.append(f"Resets in {reset_num}s")
            except ValueError:
                parts.append(f"Reset: {reset_raw}")

        if retry_after:
            details["retry_after"] = retry_after
            parts.append(f"Retry-After: {retry_after}")

        severity = Severity.INFO
        usage_pct = details.get("usage_pct", 0)
        if usage_pct >= 90:
            severity = Severity.WARNING
        if ctx.response.status_code == 429:
            severity = Severity.CRITICAL

        findings.append(Finding(
            category=self.category,
            severity=severity,
            title="Rate limit status" + (f" ({usage_pct:.0f}% used)" if usage_pct else ""),
            description=" | ".join(parts) if parts else "Rate limit headers detected.",
            analyzer_id=self.analyzer_id,
            recommendation="Back off or retry later when near limit, and consider client-side throttling.",
            details=details,
        ))
        return findings


class CachingBehaviorAnalyzer(Analyzer):
    analyzer_id = "server.caching"
    category = Category.SERVER
    display_name = "Caching Behaviour Summary"

    def analyze(self, ctx: AnalysisContext) -> List[Finding]:
        findings: List[Finding] = []
        headers = ctx.response.headers

        cache_control = headers.get("cache-control", "")
        etag = headers.get("etag", "")
        expires = headers.get("expires", "")
        vary = headers.get("vary", "")
        age = headers.get("age", "")
        pragma = headers.get("pragma", "")

        if not any((cache_control, etag, expires, vary, age, pragma)):
            return findings

        details: Dict[str, str] = {}
        parts: List[str] = []

        if cache_control:
            details["cache-control"] = cache_control
            directives = parse_cache_control(cache_control)
            if directives:
                parts.append("Cache-Control: " + ", ".join(directives))
        if etag:
            details["etag"] = etag
            parts.append("Has ETag (conditional requests supported)")
        if expires:
            details["expires"] = expires
            parts.append(f"Expires: {expires}")
        if vary:
            details["vary"] = vary
            parts.append(f"Vary: {vary}")
        if age:
            details["age"] = age
            parts.append(f"Age: {age}s (served from cache)")
        if pragma:
            details["pragma"] = pragma

        findings.append(Finding(
            category=self.category,
            severity=Severity.INFO,
            title="Caching: " + (summarize_cache_control(cache_control) if cache_control else "headers present"),
            description=" | ".join(parts),
            analyzer_id=self.analyzer_id,
            recommendation="Set explicit Cache-Control directives and validators (ETag/Last-Modified) for predictable caching.",
            details=details,
        ))
        return findings


class APIVersionDetectionAnalyzer(Analyzer):
    analyzer_id = "server.api_version"
    category = Category.SERVER
    display_name = "API Version Detection"

    _URL_VERSION_RE = re.compile(r"/v(\d+(?:\.\d+)?)(?:/|$|\?)")
    _HEADER_KEYS = ("api-version", "x-api-version", "x-version")

    def analyze(self, ctx: AnalysisContext) -> List[Finding]:
        findings: List[Finding] = []
        versions: Dict[str, str] = {}

        url = ctx.response.sent_url or ctx.request.url
        match = self._URL_VERSION_RE.search(url)
        if match:
            versions["URL path"] = f"v{match.group(1)}"

        accept = (ctx.request.headers or {}).get("Accept", "")
        if "version=" in accept:
            accept_match = re.search(r"version=([^\s;,]+)", accept)
            if accept_match:
                versions["Accept header"] = accept_match.group(1)

        for header in self._HEADER_KEYS:
            value = ctx.response.headers.get(header, "")
            if value:
                versions[f"Header ({header})"] = value

        if not versions:
            return findings

        findings.append(Finding(
            category=self.category,
            severity=Severity.INFO,
            title=f"API version: {next(iter(versions.values()))}",
            description=" | ".join([f"{src}: {version}" for src, version in versions.items()]),
            analyzer_id=self.analyzer_id,
            recommendation="Use one canonical API versioning strategy (path, header, or media type) across endpoints.",
            details=versions,
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

        values_raw = stats.get("elapsed_values", "[]")
        try:
            values = json.loads(values_raw) if isinstance(values_raw, str) else values_raw
        except Exception:
            return findings

        samples = coerce_numeric_samples(values)
        if len(samples) < 5:
            return findings

        stdev = statistics.stdev(samples)
        if stdev == 0:
            return findings

        mean = statistics.mean(samples)
        current_ms = ctx.response.elapsed * 1000
        zscore = (current_ms - mean) / stdev

        if abs(zscore) < self._ZSCORE_THRESHOLD:
            return findings

        details = {
            "current_ms": round(current_ms, 1),
            "mean_ms": round(mean, 1),
            "stdev_ms": round(stdev, 1),
            "zscore": round(zscore, 2),
            "sample_size": len(samples),
        }

        if zscore > 0:
            findings.append(Finding(
                category=self.category,
                severity=Severity.WARNING,
                title=f"Response {round(current_ms)}ms is abnormally slow (z={zscore:.1f})",
                description=(
                    f"Average for this endpoint: {round(mean)}ms +/- {round(stdev)}ms. "
                    f"Current response is {zscore:.1f} standard deviations above average."
                ),
                analyzer_id=self.analyzer_id,
                recommendation="Inspect recent backend dependencies and slow queries for this endpoint.",
                details=details,
            ))
        else:
            findings.append(Finding(
                category=self.category,
                severity=Severity.INFO,
                title=f"Response {round(current_ms)}ms is unusually fast (z={zscore:.1f})",
                description=f"Average for this endpoint: {round(mean)}ms +/- {round(stdev)}ms.",
                analyzer_id=self.analyzer_id,
                recommendation="Track whether the improvement is sustained and capture what changed.",
                details=details,
            ))

        return findings

