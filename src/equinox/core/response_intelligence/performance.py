"""Performance & Efficiency analyzers."""

from __future__ import annotations

import json
import logging
import math
from typing import List

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


class CompressionAnalyzer(Analyzer):
    analyzer_id = "perf.compression"
    category = Category.PERFORMANCE
    display_name = "Compression Analysis"

    _MIN_BODY_FOR_COMPRESSION = 1024  # 1 KB

    def analyze(self, ctx: AnalysisContext) -> List[Finding]:
        findings: List[Finding] = []
        resp = ctx.response
        size = resp.size

        encoding = resp.headers.get("content-encoding", "").lower()
        sent_accept = (ctx.request.headers or {}).get("Accept-Encoding", "")

        if encoding:
            # Compression is active — report it as info
            findings.append(Finding(
                category=self.category,
                severity=Severity.INFO,
                title=f"Response compressed ({encoding})",
                description=f"Transfer encoding: {encoding}. Body size: {self._fmt(size)}.",
                analyzer_id=self.analyzer_id,
                recommendation="Keep compression enabled for text payloads and monitor CPU impact on high-traffic endpoints.",
                details={"encoding": encoding, "body_size": size},
            ))
        elif size >= self._MIN_BODY_FOR_COMPRESSION:
            ct = resp.content_type or ""
            compressible = any(t in ct for t in (
                "json", "xml", "html", "text", "javascript", "css", "svg",
            ))
            if compressible:
                sev = Severity.WARNING if size > 10_000 else Severity.INFO
                desc = (
                    f"Response is {self._fmt(size)} but not compressed. "
                )
                if not sent_accept:
                    desc += "No Accept-Encoding header was sent in the request."
                else:
                    desc += "Server did not compress despite Accept-Encoding being sent."
                findings.append(Finding(
                    category=self.category,
                    severity=sev,
                    title="Response not compressed",
                    description=desc,
                    analyzer_id=self.analyzer_id,
                    recommendation="Enable gzip/br compression for compressible response types and include Accept-Encoding on clients.",
                    details={
                        "body_size": size,
                        "content_type": ct,
                        "accept_encoding_sent": bool(sent_accept),
                    },
                ))
        return findings

    @staticmethod
    def _fmt(n: int) -> str:
        if n >= 1_048_576:
            return f"{n / 1_048_576:.1f} MB"
        if n >= 1024:
            return f"{n / 1024:.1f} KB"
        return f"{n} B"


class TimingBreakdownAnalyzer(Analyzer):
    analyzer_id = "perf.timing_breakdown"
    category = Category.PERFORMANCE
    display_name = "Timing Breakdown"

    def analyze(self, ctx: AnalysisContext) -> List[Finding]:
        findings: List[Finding] = []
        timings = getattr(ctx.response, "timings", None)
        if not timings:
            return findings

        parts: List[str] = []
        details: dict = {}
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
                val = timings[key]
                details[key] = val
                pct = (val / total * 100) if total else 0
                parts.append(f"{label}: {val} ms ({pct:.0f}%)")

        # Identify the slowest phase
        phase_items = {k: v for k, v in details.items() if k != "total_ms" and isinstance(v, (int, float))}
        if phase_items:
            slowest = max(phase_items, key=phase_items.get)  # type: ignore[arg-type]
            details["slowest_phase"] = slowest

        sev = Severity.INFO
        ttfb = timings.get("ttfb_ms", 0)
        if ttfb > 2000:
            sev = Severity.WARNING
        elif total > 5000:
            sev = Severity.WARNING

        findings.append(Finding(
            category=self.category,
            severity=sev,
            title=f"Response time: {total} ms",
            description=" · ".join(parts) if parts else f"Total: {total} ms",
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

        elapsed_values_raw = stats.get("elapsed_values", "[]")
        try:
            values = json.loads(elapsed_values_raw) if isinstance(elapsed_values_raw, str) else elapsed_values_raw
        except Exception:
            return findings

        values = _coerce_numeric_samples(values)
        if len(values) < 3:
            if not values:
                logger.debug("ResponseTimePercentileAnalyzer: no valid numeric samples")
            return findings

        values_sorted = sorted(values)
        call_count = stats.get("call_count", len(values))
        p50 = self._percentile(values_sorted, 50)
        p95 = self._percentile(values_sorted, 95)
        p99 = self._percentile(values_sorted, 99)
        current = ctx.response.elapsed * 1000  # ms

        detail = {
            "p50_ms": round(p50, 1),
            "p95_ms": round(p95, 1),
            "p99_ms": round(p99, 1),
            "current_ms": round(current, 1),
            "call_count": call_count,
            "sample_size": len(values),
        }

        sev = Severity.INFO
        if current > p99 and len(values) >= 5:
            sev = Severity.WARNING
            logger.debug(
                "ResponseTimePercentileAnalyzer: current=%dms exceeds p99=%dms (sample=%d)",
                round(current), round(p99), len(values),
            )

        findings.append(Finding(
            category=self.category,
            severity=sev,
            title=f"P50: {detail['p50_ms']} ms · P95: {detail['p95_ms']} ms · P99: {detail['p99_ms']} ms",
            description=f"Based on {detail['sample_size']} recent calls. Current: {detail['current_ms']} ms.",
            analyzer_id=self.analyzer_id,
            recommendation="Prioritize reducing P95/P99 latency by profiling slow code paths and backend dependencies.",
            details=detail,
        ))
        return findings

    @staticmethod
    def _percentile(sorted_data: list, pct: int) -> float:
        if not sorted_data:
            return 0.0
        k = (len(sorted_data) - 1) * pct / 100
        f = int(k)
        c = f + 1
        if c >= len(sorted_data):
            return float(sorted_data[f])
        return sorted_data[f] + (k - f) * (sorted_data[c] - sorted_data[f])


class PaginationDetectionAnalyzer(Analyzer):
    analyzer_id = "perf.pagination"
    category = Category.PERFORMANCE
    display_name = "Pagination Detection"

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
            obj = ctx.response.json()
        except Exception:
            return findings
        if not isinstance(obj, dict):
            return findings

        keys = set(obj.keys())
        flat = self._flatten_keys(obj)

        detected: dict = {}
        nav = keys & self._PAGE_KEYS
        if nav:
            detected["navigation_fields"] = list(nav)
        total_k = keys & self._TOTAL_KEYS
        if total_k:
            for k in total_k:
                detected["total"] = obj[k]
                break
        page_k = keys & self._PAGE_NUM_KEYS
        if page_k:
            for k in page_k:
                detected["current_page"] = obj[k]
                break
        size_k = keys & self._PAGE_SIZE_KEYS
        if size_k:
            for k in size_k:
                detected["page_size"] = obj[k]
                break
        tp_k = keys & self._TOTAL_PAGES_KEYS
        if tp_k:
            for k in tp_k:
                detected["total_pages"] = obj[k]
                break

        # Check for Link header pagination
        link_hdr = ctx.response.headers.get("link", "")
        if 'rel="next"' in link_hdr or "rel=next" in link_hdr:
            detected["link_header_next"] = True

        if not detected:
            return findings

        # Build a human description
        parts: List[str] = []
        cp = detected.get("current_page")
        tp = detected.get("total_pages")
        total = detected.get("total")
        if cp is not None and tp is not None:
            parts.append(f"Page {cp} of {tp}")
        elif cp is not None:
            parts.append(f"Page {cp}")
        if total is not None:
            parts.append(f"{total} total items")
        if detected.get("link_header_next"):
            parts.append("Link header contains rel=next")

        findings.append(Finding(
            category=self.category,
            severity=Severity.INFO,
            title="Paginated response detected",
            description=" · ".join(parts) if parts else "Response contains pagination fields.",
            analyzer_id=self.analyzer_id,
            recommendation="Expose consistent pagination metadata (page, size, total, next cursor) across similar endpoints.",
            details=detected,
        ))
        return findings

    @staticmethod
    def _flatten_keys(obj: dict, prefix: str = "") -> set:
        keys: set = set()
        for k, v in obj.items():
            full = f"{prefix}.{k}" if prefix else k
            keys.add(full)
            if isinstance(v, dict):
                keys |= PaginationDetectionAnalyzer._flatten_keys(v, full)
        return keys

