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

logger = logging.getLogger(__name__)


class DeprecatedAPIAnalyzer(Analyzer):
    analyzer_id = "hints.deprecated"
    category = Category.HINTS
    display_name = "Deprecated API Warnings"

    def analyze(self, ctx: AnalysisContext) -> List[Finding]:
        findings: List[Finding] = []
        hdrs = ctx.response.headers

        deprecation = hdrs.get("deprecation", "")
        sunset = hdrs.get("sunset", "")
        warning_hdr = hdrs.get("warning", "")

        signals: List[str] = []
        detail: Dict[str, str] = {}

        if deprecation:
            detail["deprecation"] = deprecation
            signals.append(f"Deprecation: {deprecation}")
        if sunset:
            detail["sunset"] = sunset
            signals.append(f"Sunset: {sunset}")
        if warning_hdr:
            detail["warning"] = warning_hdr
            # Only flag if it looks like a deprecation warning
            if any(w in warning_hdr.lower() for w in ("deprecated", "obsolete", "sunset", "removed")):
                signals.append(f"Warning: {warning_hdr}")

        if not signals:
            return findings

        sev = Severity.WARNING
        if sunset:
            sev = Severity.CRITICAL

        findings.append(Finding(
            category=self.category,
            severity=sev,
            title="API deprecation notice",
            description=" · ".join(signals),
            analyzer_id=self.analyzer_id,
            details=detail,
        ))
        return findings


class SuggestedEncodingAnalyzer(Analyzer):
    analyzer_id = "hints.accept_encoding"
    category = Category.HINTS
    display_name = "Suggested Accept-Encoding"

    def analyze(self, ctx: AnalysisContext) -> List[Finding]:
        findings: List[Finding] = []
        sent_ae = (ctx.request.headers or {}).get("Accept-Encoding", "")
        resp_encoding = ctx.response.headers.get("content-encoding", "")
        size = ctx.response.size

        if sent_ae or resp_encoding or size < 1024:
            return findings

        ct = ctx.response.content_type or ""
        compressible = any(t in ct for t in (
            "json", "xml", "html", "text", "javascript", "css", "svg",
        ))
        if not compressible:
            return findings

        findings.append(Finding(
            category=self.category,
            severity=Severity.INFO,
            title="Consider adding Accept-Encoding header",
            description=(
                f"Request did not include Accept-Encoding. The {self._fmt(size)} "
                f"response ({ct}) could likely be compressed."
            ),
            analyzer_id=self.analyzer_id,
            details={"body_size": size, "content_type": ct},
        ))
        return findings

    @staticmethod
    def _fmt(n: int) -> str:
        if n >= 1_048_576:
            return f"{n / 1_048_576:.1f} MB"
        if n >= 1024:
            return f"{n / 1024:.1f} KB"
        return f"{n} B"


class NPlusOneDetectionAnalyzer(Analyzer):
    analyzer_id = "hints.n_plus_one"
    category = Category.HINTS
    display_name = "N+1 Request Pattern Detection"

    _THRESHOLD = 4  # minimum similar sequential requests to flag

    def analyze(self, ctx: AnalysisContext) -> List[Finding]:
        findings: List[Finding] = []
        history = ctx.history_rows
        if not history or len(history) < self._THRESHOLD:
            return findings

        # Group consecutive requests by normalised URL pattern
        runs: List[Tuple[str, int]] = []
        current_pattern = ""
        current_count = 0

        for row in history:
            url = row.get("url", "")
            method = row.get("method", "GET")
            pattern = self._normalise(url)
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

        for pattern, count in runs:
            logger.debug(
                "NPlusOneDetectionAnalyzer: possible N+1 — pattern=%r count=%d",
                pattern, count,
            )
            findings.append(Finding(
                category=self.category,
                severity=Severity.WARNING,
                title=f"Possible N+1 pattern: {count} sequential calls",
                description=(
                    f'"{pattern}" was called {count} times in a row. '
                    f"Consider batching or using a list endpoint."
                ),
                analyzer_id=self.analyzer_id,
                details={"pattern": pattern, "count": count},
            ))
        return findings

    @staticmethod
    def _normalise(url: str) -> str:
        """Collapse numeric/UUID segments for grouping using centralised URL helpers."""
        parts = urls.normalized_parts(url)
        segs = parts.get("path_segments") or []
        return "/" + "/".join(segs) if segs else "/"


class ResponseEncodingIssuesAnalyzer(Analyzer):
    analyzer_id = "hints.encoding_issues"
    category = Category.HINTS
    display_name = "Response Encoding Issues"

    # UTF-8 BOM
    _BOM = b"\xef\xbb\xbf"
    # Common mojibake sequences (UTF-8 bytes interpreted as Latin-1)
    _MOJIBAKE_PATTERNS = [
        re.compile(r"Ã¤|Ã¶|Ã¼|Ã©|Ã¨|Ã\xa0|Ã±|Ã§"),  # Diacritics misread
        re.compile(r"\x00[^\x00]"),  # Null bytes in text (possible UTF-16)
    ]

    def analyze(self, ctx: AnalysisContext) -> List[Finding]:
        findings: List[Finding] = []
        body = ctx.response.body
        if not body:
            return findings

        issues: List[str] = []
        detail: Dict[str, object] = {}

        # BOM check
        if body[:3] == self._BOM:
            issues.append("UTF-8 BOM present — may cause JSON parsing issues")
            detail["bom"] = True

        # Mojibake check (only on text content)
        ct = ctx.response.content_type or ""
        if any(t in ct for t in ("json", "xml", "html", "text")):
            text = ctx.response.text[:50_000]
            for pat in self._MOJIBAKE_PATTERNS:
                if pat.search(text):
                    issues.append("Possible mojibake (encoding mismatch) detected")
                    detail["mojibake"] = True
                    break

        # charset declared vs actual
        declared = ctx.response.encoding
        ct_raw = ctx.response.headers.get("content-type", "")
        if declared and declared.lower() not in ("utf-8", "utf8") and "json" in ct:
            issues.append(f"JSON response declares charset={declared} (expected utf-8)")
            detail["declared_charset"] = declared

        if not issues:
            return findings

        findings.append(Finding(
            category=self.category,
            severity=Severity.WARNING if detail.get("mojibake") else Severity.INFO,
            title="Response encoding issue" + ("s" if len(issues) > 1 else ""),
            description=" · ".join(issues),
            analyzer_id=self.analyzer_id,
            details=detail,
        ))
        return findings


class LinkHeaderParsingAnalyzer(Analyzer):
    analyzer_id = "hints.link_header"
    category = Category.HINTS
    display_name = "Link Header Parsing"

    _LINK_RE = re.compile(r'<([^>]+)>\s*;\s*rel="?([^",]+)"?')

    def analyze(self, ctx: AnalysisContext) -> List[Finding]:
        findings: List[Finding] = []
        link_hdr = ctx.response.headers.get("link", "")
        if not link_hdr:
            return findings

        links: List[Dict[str, str]] = []
        for m in self._LINK_RE.finditer(link_hdr):
            links.append({"url": m.group(1), "rel": m.group(2).strip()})

        if not links:
            return findings

        parts = [f'{l["rel"]}: {l["url"]}' for l in links[:5]]
        findings.append(Finding(
            category=self.category,
            severity=Severity.INFO,
            title=f"{len(links)} Link relation(s) found",
            description=" · ".join(parts),
            analyzer_id=self.analyzer_id,
            details={"links": links},
        ))
        return findings

