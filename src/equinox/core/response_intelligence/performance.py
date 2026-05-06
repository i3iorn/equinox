"""Performance & Efficiency analyzers."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from equinox.core.response_intelligence.base import Analyzer
from equinox.core.response_intelligence.models import (
    AnalysisContext,
    Category,
    Finding,
    Severity,
)
from equinox.core.response_intelligence.shared.content import (
    format_bytes,
    is_compressible_content_type,
)
from equinox.core.response_intelligence.shared.stats import (
    coerce_numeric_samples,
    percentile,
)

logger = logging.getLogger(__name__)


class CompressionAnalyzer(Analyzer):
    analyzer_id = "perf.compression"
    category = Category.PERFORMANCE
    display_name = "Compression Analysis"

    _MIN_BODY_FOR_COMPRESSION = 1024

    def analyze(self, ctx: AnalysisContext) -> List[Finding]:
        findings: List[Finding] = []
        response = ctx.response
        size = response.size
        encoding = response.headers.get("content-encoding", "").lower()
        sent_accept = (ctx.request.headers or {}).get("Accept-Encoding", "")

        if encoding:
            findings.append(Finding(
                category=self.category,
                severity=Severity.INFO,
                title=f"Response compressed ({encoding})",
                description=f"Transfer encoding: {encoding}. Body size: {format_bytes(size)}.",
                analyzer_id=self.analyzer_id,
                recommendation="Keep compression enabled for text payloads and monitor CPU impact on high-traffic endpoints.",
                details={"encoding": encoding, "body_size": size},
            ))
            return findings

        if size < self._MIN_BODY_FOR_COMPRESSION:
            return findings

        content_type = response.content_type or ""
        if not is_compressible_content_type(content_type):
            return findings

        severity = Severity.WARNING if size > 10_000 else Severity.INFO
        description = f"Response is {format_bytes(size)} but not compressed. "
        if not sent_accept:
            description += "No Accept-Encoding header was sent in the request."
        else:
            description += "Server did not compress despite Accept-Encoding being sent."

        findings.append(Finding(
            category=self.category,
            severity=severity,
            title="Response not compressed",
            description=description,
            analyzer_id=self.analyzer_id,
            recommendation="Enable gzip/br compression for compressible response types and include Accept-Encoding on clients.",
            details={
                "body_size": size,
                "content_type": content_type,
                "accept_encoding_sent": bool(sent_accept),
            },
        ))
        return findings


class TimingBreakdownAnalyzer(Analyzer):
    analyzer_id = "perf.timing_breakdown"
    category = Category.PERFORMANCE
    display_name = "Timing Breakdown"

    def analyze(self, ctx: AnalysisContext) -> List[Finding]:
        findings: List[Finding] = []
        timings = getattr(ctx.response, "timings", None)
        if not timings:
            return findings

        details: Dict[str, Any] = {}
        parts: List[str] = []

        total = timings.get("total_ms", int(ctx.response.elapsed * 1000))
        details["total_ms"] = total

        for key, label in (
            ("dns_ms", "DNS"),
            ("connect_ms", "Connect"),
            ("tls_ms", "TLS"),
            ("ttfb_ms", "TTFB"),
            ("transfer_ms", "Transfer"),
        ):
            if key in timings:
                value = timings[key]
                details[key] = value
                pct = (value / total * 100) if total else 0
                parts.append(f"{label}: {value} ms ({pct:.0f}%)")

        phase_values = {
            key: value
            for key, value in details.items()
            if key != "total_ms" and isinstance(value, (int, float))
        }
        if phase_values:
            slowest_phase, _ = max(phase_values.items(), key=lambda item: item[1])
            details["slowest_phase"] = slowest_phase

        severity = Severity.INFO
        if timings.get("ttfb_ms", 0) > 2000 or total > 5000:
            severity = Severity.WARNING

        findings.append(Finding(
            category=self.category,
            severity=severity,
            title=f"Response time: {total} ms",
            description=" | ".join(parts) if parts else f"Total: {total} ms",
            analyzer_id=self.analyzer_id,
            recommendation="Investigate the slowest timing phase first (DNS/connect/TLS/TTFB/transfer) to reduce latency.",
            details=details,
        ))
        return findings


class ResponseTimePercentileAnalyzer(Analyzer):
    analyzer_id = "perf.percentiles"
    category = Category.PERFORMANCE
    display_name = "Response Time Percentiles"

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
        if len(samples) < 3:
            if not samples:
                logger.debug("ResponseTimePercentileAnalyzer: no valid numeric samples")
            return findings

        sorted_samples = sorted(samples)
        p50 = percentile(sorted_samples, 50)
        p95 = percentile(sorted_samples, 95)
        p99 = percentile(sorted_samples, 99)
        current = ctx.response.elapsed * 1000

        details = {
            "p50_ms": round(p50, 1),
            "p95_ms": round(p95, 1),
            "p99_ms": round(p99, 1),
            "current_ms": round(current, 1),
            "sample_size": len(samples),
            "call_count": stats.get("call_count", len(samples)),
        }

        severity = Severity.INFO
        if current > p99 and len(samples) >= 5:
            severity = Severity.WARNING

        findings.append(Finding(
            category=self.category,
            severity=severity,
            title=f"P50: {details['p50_ms']} ms | P95: {details['p95_ms']} ms | P99: {details['p99_ms']} ms",
            description=f"Based on {details['sample_size']} recent calls. Current: {details['current_ms']} ms.",
            analyzer_id=self.analyzer_id,
            recommendation="Prioritize reducing P95/P99 latency by profiling slow code paths and backend dependencies.",
            details=details,
        ))
        return findings


class PaginationDetectionAnalyzer(Analyzer):
    analyzer_id = "perf.pagination"
    category = Category.PERFORMANCE
    display_name = "Pagination Detection"
    requires_valid_json_body = True

    _PAGE_KEYS = {"next", "previous", "prev", "cursor", "next_cursor", "after", "before"}
    _TOTAL_KEYS = {"total", "total_count", "totalCount", "count", "total_items", "totalItems"}
    _PAGE_NUM_KEYS = {"page", "current_page", "currentPage", "page_number", "pageNumber"}
    _PAGE_SIZE_KEYS = {"per_page", "perPage", "page_size", "pageSize", "limit", "size"}
    _TOTAL_PAGES_KEYS = {"total_pages", "totalPages", "page_count", "pageCount", "last_page", "lastPage"}

    def analyze(self, ctx: AnalysisContext) -> List[Finding]:
        findings: List[Finding] = []
        if not ctx.response.is_json:
            return findings
        try:
            body = ctx.response.json()
        except Exception:
            return findings
        if not isinstance(body, dict):
            return findings

        keys = set(body.keys())
        detected: Dict[str, object] = {}

        nav = keys & self._PAGE_KEYS
        if nav:
            detected["navigation_fields"] = sorted(nav)

        for alias_group, output_key in (
            (self._TOTAL_KEYS, "total"),
            (self._PAGE_NUM_KEYS, "current_page"),
            (self._PAGE_SIZE_KEYS, "page_size"),
            (self._TOTAL_PAGES_KEYS, "total_pages"),
        ):
            for key in alias_group:
                if key in body:
                    detected[output_key] = body[key]
                    break

        link_header = ctx.response.headers.get("link", "")
        if 'rel="next"' in link_header or "rel=next" in link_header:
            detected["link_header_next"] = True

        if not detected:
            return findings

        parts: List[str] = []
        current_page = detected.get("current_page")
        total_pages = detected.get("total_pages")
        total_items = detected.get("total")
        if current_page is not None and total_pages is not None:
            parts.append(f"Page {current_page} of {total_pages}")
        elif current_page is not None:
            parts.append(f"Page {current_page}")
        if total_items is not None:
            parts.append(f"{total_items} total items")
        if detected.get("link_header_next"):
            parts.append("Link header contains rel=next")

        findings.append(Finding(
            category=self.category,
            severity=Severity.INFO,
            title="Paginated response detected",
            description=" | ".join(parts) if parts else "Response contains pagination fields.",
            analyzer_id=self.analyzer_id,
            recommendation="Expose consistent pagination metadata (page, size, total, next cursor) across similar endpoints.",
            details=detected,
        ))
        return findings

