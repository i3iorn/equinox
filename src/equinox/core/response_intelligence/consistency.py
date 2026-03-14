"""Consistency & Correctness analyzers."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Set, Tuple

from equinox.core.response_intelligence.base import Analyzer
from equinox.core.response_intelligence.models import (
    AnalysisContext,
    Category,
    Finding,
    Severity,
)


class StatusBodyMismatchAnalyzer(Analyzer):
    analyzer_id = "consistency.status_body"
    category = Category.CONSISTENCY
    display_name = "Status Code vs Body Mismatch"

    def analyze(self, ctx: AnalysisContext) -> List[Finding]:
        findings: List[Finding] = []
        code = ctx.response.status_code
        body = ctx.response.body

        # 204 No Content but body present
        if code == 204 and body and len(body.strip()) > 0:
            findings.append(Finding(
                category=self.category,
                severity=Severity.WARNING,
                title="204 No Content with non-empty body",
                description=f"Status 204 should have no body, but response has {len(body)} bytes.",
                analyzer_id=self.analyzer_id,
                details={"status_code": code, "body_size": len(body)},
            ))

        # 201 Created with empty body
        if code == 201 and (not body or len(body.strip()) == 0):
            findings.append(Finding(
                category=self.category,
                severity=Severity.INFO,
                title="201 Created with empty body",
                description="A 201 response typically includes the created resource or a Location header.",
                analyzer_id=self.analyzer_id,
                details={"status_code": code, "has_location": "location" in ctx.response.headers},
            ))

        # 2xx success but body contains error-like keys
        if 200 <= code < 300 and ctx.response.is_json:
            try:
                obj = ctx.response.json()
                if isinstance(obj, dict):
                    error_keys = {"error", "errors", "error_message", "errorMessage",
                                  "fault", "exception"}
                    found = set(obj.keys()) & error_keys
                    if found:
                        # Check the value is truthy (not null/empty)
                        truthy = [k for k in found if obj[k]]
                        if truthy:
                            findings.append(Finding(
                                category=self.category,
                                severity=Severity.WARNING,
                                title=f"{code} success but body contains error fields",
                                description=f"Found error-related keys: {', '.join(truthy)}.",
                                analyzer_id=self.analyzer_id,
                                details={"status_code": code, "error_keys": list(truthy)},
                            ))
            except Exception:
                pass

        return findings


class ContentTypeMismatchAnalyzer(Analyzer):
    analyzer_id = "consistency.content_type"
    category = Category.CONSISTENCY
    display_name = "Content-Type vs Body Mismatch"

    def analyze(self, ctx: AnalysisContext) -> List[Finding]:
        findings: List[Finding] = []
        ct = ctx.response.content_type or ""
        body = ctx.response.body
        if not body or not ct:
            return findings

        text = ctx.response.text[:2048]

        # Claims JSON but isn't
        if "json" in ct:
            try:
                json.loads(text)
            except (json.JSONDecodeError, ValueError):
                findings.append(Finding(
                    category=self.category,
                    severity=Severity.WARNING,
                    title="Content-Type says JSON but body is not valid JSON",
                    description=f"Content-Type: {ct}",
                    analyzer_id=self.analyzer_id,
                    details={"content_type": ct},
                ))

        # Claims XML but doesn't look like it
        elif "xml" in ct:
            stripped = text.lstrip()
            if stripped and not stripped.startswith("<"):
                findings.append(Finding(
                    category=self.category,
                    severity=Severity.WARNING,
                    title="Content-Type says XML but body doesn't start with '<'",
                    description=f"Content-Type: {ct}",
                    analyzer_id=self.analyzer_id,
                    details={"content_type": ct},
                ))

        # Claims HTML but doesn't look like it
        elif "html" in ct:
            stripped = text.lstrip().lower()
            if stripped and not (stripped.startswith("<") or stripped.startswith("<!doctype")):
                findings.append(Finding(
                    category=self.category,
                    severity=Severity.INFO,
                    title="Content-Type says HTML but body doesn't look like HTML",
                    description=f"Content-Type: {ct}",
                    analyzer_id=self.analyzer_id,
                    details={"content_type": ct},
                ))

        return findings


class DuplicateJsonKeysAnalyzer(Analyzer):
    analyzer_id = "consistency.duplicate_keys"
    category = Category.CONSISTENCY
    display_name = "Duplicate JSON Keys"

    _MAX_SCAN = 512_000

    def analyze(self, ctx: AnalysisContext) -> List[Finding]:
        findings: List[Finding] = []
        if not ctx.response.is_json or not ctx.response.body:
            return findings

        text = ctx.response.text[:self._MAX_SCAN]
        dupes: List[str] = []

        def detect_dupes(pairs: list) -> dict:
            seen: Dict[str, int] = {}
            for key, _val in pairs:
                seen[key] = seen.get(key, 0) + 1
            for k, count in seen.items():
                if count > 1 and k not in dupes:
                    dupes.append(k)
            return dict(pairs)

        try:
            json.loads(text, object_pairs_hook=detect_dupes)
        except Exception:
            return findings

        if dupes:
            findings.append(Finding(
                category=self.category,
                severity=Severity.WARNING,
                title=f"{len(dupes)} duplicate JSON key(s)",
                description=f"Duplicate keys: {', '.join(dupes[:10])}. "
                            "Behaviour varies across parsers.",
                analyzer_id=self.analyzer_id,
                details={"duplicate_keys": dupes[:20]},
            ))
        return findings


# Patterns for date-like strings
_DATE_PATTERNS: List[Tuple[str, re.Pattern]] = [
    ("ISO 8601", re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")),
    ("ISO date only", re.compile(r'"\d{4}-\d{2}-\d{2}"')),
    ("Unix timestamp (s)", re.compile(r':\s*1[5-9]\d{8}(?:\.\d+)?(?:\s*[,}\]])')),
    ("Unix timestamp (ms)", re.compile(r':\s*1[5-9]\d{11}(?:\s*[,}\]])')),
    ("RFC 2822", re.compile(r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun),?\s+\d{1,2}\s+\w{3}\s+\d{4}")),
    ("US date (MM/DD/YYYY)", re.compile(r"\b\d{1,2}/\d{1,2}/\d{4}\b")),
]


class DateFormatInconsistencyAnalyzer(Analyzer):
    analyzer_id = "consistency.date_formats"
    category = Category.CONSISTENCY
    display_name = "Date Format Inconsistency"

    _MAX_SCAN = 256_000

    def analyze(self, ctx: AnalysisContext) -> List[Finding]:
        findings: List[Finding] = []
        if not ctx.response.body:
            return findings

        text = ctx.response.text[:self._MAX_SCAN]
        formats_found: List[str] = []
        for label, pat in _DATE_PATTERNS:
            if pat.search(text):
                formats_found.append(label)

        if len(formats_found) >= 2:
            findings.append(Finding(
                category=self.category,
                severity=Severity.INFO,
                title="Mixed date formats detected",
                description=f"Found: {', '.join(formats_found)}. "
                            "Consider standardising on a single format (e.g. ISO 8601).",
                analyzer_id=self.analyzer_id,
                details={"formats": formats_found},
            ))
        return findings


class NullVsMissingAnalyzer(Analyzer):
    analyzer_id = "consistency.null_vs_missing"
    category = Category.CONSISTENCY
    display_name = "Null vs Missing Field Patterns"

    def analyze(self, ctx: AnalysisContext) -> List[Finding]:
        findings: List[Finding] = []
        if not ctx.response.is_json:
            return findings
        try:
            obj = ctx.response.json()
        except Exception:
            return findings

        # Only meaningful for arrays of objects (e.g. list endpoints)
        items = obj if isinstance(obj, list) else obj.get("data", obj.get("results", obj.get("items", [])))
        if not isinstance(items, list) or len(items) < 2:
            return findings

        # Collect only dicts
        dicts = [d for d in items if isinstance(d, dict)]
        if len(dicts) < 2:
            return findings

        all_keys: Set[str] = set()
        for d in dicts:
            all_keys |= d.keys()

        inconsistent: List[str] = []
        for key in sorted(all_keys):
            has_null = False
            is_missing = False
            for d in dicts:
                if key not in d:
                    is_missing = True
                elif d[key] is None:
                    has_null = True
            if has_null and is_missing:
                inconsistent.append(key)

        if inconsistent:
            findings.append(Finding(
                category=self.category,
                severity=Severity.INFO,
                title=f"{len(inconsistent)} field(s) use both null and absent",
                description=f"Fields: {', '.join(inconsistent[:10])}. "
                            "Consider using one convention (null or omit).",
                analyzer_id=self.analyzer_id,
                details={"fields": inconsistent[:20]},
            ))
        return findings


class SchemaDriftAnalyzer(Analyzer):
    analyzer_id = "consistency.schema_drift"
    category = Category.CONSISTENCY
    display_name = "Schema Drift Detection"

    def analyze(self, ctx: AnalysisContext) -> List[Finding]:
        findings: List[Finding] = []
        if not ctx.response.is_json:
            return findings
        if ctx.stored_schema is None:
            return findings

        try:
            obj = ctx.response.json()
        except Exception:
            return findings

        current = self.build_schema_fingerprint(obj)
        stored = ctx.stored_schema

        added = set(current.keys()) - set(stored.keys())
        removed = set(stored.keys()) - set(current.keys())
        type_changed: List[str] = []
        for k in set(current.keys()) & set(stored.keys()):
            if current[k] != stored[k]:
                type_changed.append(f"{k}: {stored[k]} → {current[k]}")

        if not added and not removed and not type_changed:
            return findings

        parts: List[str] = []
        if added:
            parts.append(f"Added: {', '.join(sorted(added)[:5])}")
        if removed:
            parts.append(f"Removed: {', '.join(sorted(removed)[:5])}")
        if type_changed:
            parts.append(f"Type changed: {', '.join(type_changed[:5])}")

        sev = Severity.WARNING if removed or type_changed else Severity.INFO

        findings.append(Finding(
            category=self.category,
            severity=sev,
            title="Response schema changed since last call",
            description=" · ".join(parts),
            analyzer_id=self.analyzer_id,
            details={
                "added": sorted(added)[:20],
                "removed": sorted(removed)[:20],
                "type_changed": type_changed[:20],
            },
        ))
        return findings

    @staticmethod
    def build_schema_fingerprint(obj: Any, prefix: str = "") -> Dict[str, str]:
        """Build a flat dict mapping dotted-path → type-name for schema comparison."""
        result: Dict[str, str] = {}
        if isinstance(obj, dict):
            for k, v in obj.items():
                path = f"{prefix}.{k}" if prefix else k
                result[path] = type(v).__name__
                if isinstance(v, (dict, list)):
                    result.update(SchemaDriftAnalyzer.build_schema_fingerprint(v, path))
        elif isinstance(obj, list) and obj:
            # Fingerprint the first element
            result.update(SchemaDriftAnalyzer.build_schema_fingerprint(obj[0], f"{prefix}[]"))
        return result

