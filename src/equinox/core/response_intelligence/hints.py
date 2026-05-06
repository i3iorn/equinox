"""Developer Hints analyzers."""

from __future__ import annotations

import logging
import re
from typing import Dict, List, Tuple

from equinox.core import urls
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

logger = logging.getLogger(__name__)


class DeprecatedAPIAnalyzer(Analyzer):
    analyzer_id = "hints.deprecated"
    category = Category.HINTS
    display_name = "Deprecated API Warnings"

    def analyze(self, ctx: AnalysisContext) -> List[Finding]:
        findings: List[Finding] = []
        headers = ctx.response.headers

        deprecation = headers.get("deprecation", "")
        sunset = headers.get("sunset", "")
        warning_header = headers.get("warning", "")

        signals: List[str] = []
        details: Dict[str, str] = {}

        if deprecation:
            details["deprecation"] = deprecation
            signals.append(f"Deprecation: {deprecation}")
        if sunset:
            details["sunset"] = sunset
            signals.append(f"Sunset: {sunset}")
        if warning_header:
            details["warning"] = warning_header
            if any(token in warning_header.lower() for token in ("deprecated", "obsolete", "sunset", "removed")):
                signals.append(f"Warning: {warning_header}")

        if not signals:
            return findings

        severity = Severity.CRITICAL if sunset else Severity.WARNING
        findings.append(Finding(
            category=self.category,
            severity=severity,
            title="API deprecation notice",
            description=" | ".join(signals),
            analyzer_id=self.analyzer_id,
            recommendation="Plan migration to the replacement endpoint/version before the sunset date.",
            details=details,
        ))
        return findings


class SuggestedEncodingAnalyzer(Analyzer):
    analyzer_id = "hints.accept_encoding"
    category = Category.HINTS
    display_name = "Suggested Accept-Encoding"

    def analyze(self, ctx: AnalysisContext) -> List[Finding]:
        findings: List[Finding] = []
        sent_accept_encoding = (ctx.request.headers or {}).get("Accept-Encoding", "")
        response_encoding = ctx.response.headers.get("content-encoding", "")
        size = ctx.response.size

        if sent_accept_encoding or response_encoding or size < 1024:
            return findings

        content_type = ctx.response.content_type or ""
        if not is_compressible_content_type(content_type):
            return findings

        findings.append(Finding(
            category=self.category,
            severity=Severity.INFO,
            title="Consider adding Accept-Encoding header",
            description=(
                f"Request did not include Accept-Encoding. The {format_bytes(size)} "
                f"response ({content_type}) could likely be compressed."
            ),
            analyzer_id=self.analyzer_id,
            recommendation="Send Accept-Encoding: gzip, br on clients calling large text-based endpoints.",
            details={"body_size": size, "content_type": content_type},
        ))
        return findings


class NPlusOneDetectionAnalyzer(Analyzer):
    analyzer_id = "hints.n_plus_one"
    category = Category.HINTS
    display_name = "N+1 Request Pattern Detection"

    _THRESHOLD = 4

    def analyze(self, ctx: AnalysisContext) -> List[Finding]:
        findings: List[Finding] = []
        history_rows = ctx.history_rows
        if not history_rows or len(history_rows) < self._THRESHOLD:
            return findings

        runs: List[Tuple[str, int]] = []
        current_pattern = ""
        current_count = 0

        for row in history_rows:
            url = row.get("url", "")
            method = row.get("method", "GET")
            pattern = self._normalize(url)
            key = f"{method} {pattern}"
            if key == current_pattern:
                current_count += 1
            else:
                if current_count >= self._THRESHOLD:
                    runs.append((current_pattern, current_count))
                current_pattern = key
                current_count = 1

        if current_count >= self._THRESHOLD:
            runs.append((current_pattern, current_count))

        grouped: Dict[str, List[int]] = {}
        for pattern, count in runs:
            grouped.setdefault(pattern, []).append(count)

        for pattern in sorted(grouped.keys()):
            counts = grouped[pattern]
            bursts = len(counts)
            total_calls = sum(counts)
            n_min = min(counts)
            n_max = max(counts)
            n_label = f"N={n_min}" if n_min == n_max else f"N={n_min}-{n_max}"

            findings.append(Finding(
                category=self.category,
                severity=Severity.WARNING,
                title=f"Possible N+1 pattern: {bursts} burst(s)",
                description=(
                    f'"{pattern}" repeated across {bursts} burst(s) '
                    f"({n_label}, total {total_calls} calls). Consider batching or using a list endpoint."
                ),
                analyzer_id=self.analyzer_id,
                recommendation="Replace per-item calls with bulk/list endpoints or batch loading.",
                details={
                    "pattern": pattern,
                    "counts": counts,
                    "bursts": bursts,
                    "n_min": n_min,
                    "n_max": n_max,
                    "total_calls": total_calls,
                },
            ))
        return findings

    @staticmethod
    def _normalize(url: str) -> str:
        parts = urls.normalized_parts(url)
        segments = parts.get("path_segments") or []
        return "/" + "/".join(segments) if segments else "/"


class ResponseEncodingIssuesAnalyzer(Analyzer):
    analyzer_id = "hints.encoding_issues"
    category = Category.HINTS
    display_name = "Response Encoding Issues"

    _BOM = b"\xef\xbb\xbf"
    _MOJIBAKE_PATTERNS = [
        re.compile(r"A\u0192A\u00a4|A\u0192A\u00b6|A\u0192A\u00bc|A\u0192A\u00a9|A\u0192A\u00a8"),
        re.compile(r"\x00[^\x00]"),
    ]

    def analyze(self, ctx: AnalysisContext) -> List[Finding]:
        findings: List[Finding] = []
        body = ctx.response.body
        if not body:
            return findings

        issues: List[str] = []
        details: Dict[str, object] = {}

        if body[:3] == self._BOM:
            issues.append("UTF-8 BOM present - may cause JSON parsing issues")
            details["bom"] = True

        content_type = ctx.response.content_type or ""
        if any(token in content_type for token in ("json", "xml", "html", "text")):
            text = ctx.response.text[:50_000]
            for pattern in self._MOJIBAKE_PATTERNS:
                if pattern.search(text):
                    issues.append("Possible mojibake (encoding mismatch) detected")
                    details["mojibake"] = True
                    break

        declared = ctx.response.encoding
        if declared and declared.lower() not in ("utf-8", "utf8") and "json" in content_type:
            issues.append(f"JSON response declares charset={declared} (expected utf-8)")
            details["declared_charset"] = declared

        if not issues:
            return findings

        findings.append(Finding(
            category=self.category,
            severity=Severity.WARNING if details.get("mojibake") else Severity.INFO,
            title="Response encoding issue" + ("s" if len(issues) > 1 else ""),
            description=" | ".join(issues),
            analyzer_id=self.analyzer_id,
            recommendation="Standardize response encoding to UTF-8 and ensure Content-Type charset matches payload bytes.",
            details=details,
        ))
        return findings


class LinkHeaderParsingAnalyzer(Analyzer):
    analyzer_id = "hints.link_header"
    category = Category.HINTS
    display_name = "Link Header Parsing"

    _LINK_RE = re.compile(r'<([^>]+)>\s*;\s*rel="?([^",]+)"?')

    def analyze(self, ctx: AnalysisContext) -> List[Finding]:
        findings: List[Finding] = []
        link_header = ctx.response.headers.get("link", "")
        if not link_header:
            return findings

        links: List[Dict[str, str]] = []
        for match in self._LINK_RE.finditer(link_header):
            links.append({"url": match.group(1), "rel": match.group(2).strip()})

        if not links:
            return findings

        findings.append(Finding(
            category=self.category,
            severity=Severity.INFO,
            title=f"{len(links)} Link relation(s) found",
            description=" | ".join([f"{link['rel']}: {link['url']}" for link in links[:5]]),
            analyzer_id=self.analyzer_id,
            recommendation="Use Link headers consistently for pagination and include rel=next/prev where applicable.",
            details={"links": links},
        ))
        return findings

