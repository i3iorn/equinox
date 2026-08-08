"""Consistency & Correctness analyzers."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from equinox.core.response_intelligence.base import Analyzer
from equinox.core.response_intelligence.models import AnalysisContext
from equinox.core.response_intelligence.models import Category
from equinox.core.response_intelligence.models import Finding
from equinox.core.response_intelligence.models import Severity

logger = logging.getLogger(__name__)


class StatusBodyMismatchAnalyzer(Analyzer):  # type: ignore[misc]
    analyzer_id = "consistency.status_body"
    category = Category.CONSISTENCY
    display_name = "Status Code vs Body Mismatch"

    _NO_BODY_STATUS_CODES = {204, 205, 304}
    _ERROR_KEYS = {"error", "errors", "error_message", "errormessage", "fault", "exception"}
    _MAX_JSON_SCAN_BYTES = 512_000

    def analyze(self, ctx: AnalysisContext) -> list[Finding]:
        findings: list[Finding] = []
        status_code = ctx.response.status_code
        body = ctx.response.body

        if status_code in self._NO_BODY_STATUS_CODES and body and len(body.strip()) > 0:
            findings.append(
                Finding(
                    category=self.category,
                    severity=Severity.WARNING,
                    title=f"{status_code} response with non-empty body",
                    description=f"Status {status_code} should have no body, but response has {len(body)} bytes.",
                    analyzer_id=self.analyzer_id,
                    recommendation="Return an empty body for no-content responses, or use a status code that allows payload content.",
                    details={"status_code": status_code, "body_size": len(body)},
                ),
            )

        if status_code == 201 and (not body or len(body.strip()) == 0):
            findings.append(
                Finding(
                    category=self.category,
                    severity=Severity.INFO,
                    title="201 Created with empty body",
                    description="A 201 response typically includes the created resource or a Location header.",
                    analyzer_id=self.analyzer_id,
                    recommendation="Return the created resource or include a Location header pointing to it.",
                    details={
                        "status_code": status_code,
                        "has_location": "location" in ctx.response.headers,
                    },
                ),
            )

        if 200 <= status_code < 300 and ctx.response.is_json:
            try:
                if len(ctx.response.body) <= self._MAX_JSON_SCAN_BYTES:
                    payload = ctx.response.json()
                    error_paths = self._find_truthy_error_paths(payload)
                    if error_paths:
                        findings.append(
                            Finding(
                                category=self.category,
                                severity=Severity.WARNING,
                                title=f"{status_code} success but body contains error fields",
                                description=f"Found error-related keys: {', '.join(error_paths[:5])}.",
                                analyzer_id=self.analyzer_id,
                                recommendation="Align HTTP status codes with payload semantics and avoid error objects on successful responses.",
                                details={
                                    "status_code": status_code,
                                    "error_paths": error_paths[:20],
                                },
                            ),
                        )
            except Exception:
                logger.exception(
                    "StatusBodyMismatchAnalyzer: failed to parse JSON body",
                    exc_info=True,
                )

        return findings

    @classmethod
    def _find_truthy_error_paths(cls, value: Any, path: str = "", depth: int = 0) -> list[str]:
        if depth > 5:
            return []

        found: list[str] = []
        if isinstance(value, dict):
            for key, nested in value.items():
                key_name = str(key)
                nested_path = f"{path}.{key_name}" if path else key_name
                if key_name.lower() in cls._ERROR_KEYS and nested:
                    found.append(nested_path)
                found.extend(cls._find_truthy_error_paths(nested, nested_path, depth + 1))
        elif isinstance(value, list):
            for idx, item in enumerate(value[:25]):
                nested_path = f"{path}[{idx}]" if path else f"[{idx}]"
                found.extend(cls._find_truthy_error_paths(item, nested_path, depth + 1))

        return found


class ContentTypeMismatchAnalyzer(Analyzer):  # type: ignore[misc]
    analyzer_id = "consistency.content_type"
    category = Category.CONSISTENCY
    display_name = "Content-Type vs Body Mismatch"

    _MAX_JSON_VALIDATE_SIZE = 1_000_000

    @staticmethod
    def _looks_like_json_prefix(text: str) -> bool:
        stripped = (text or "").lstrip()
        return bool(stripped) and stripped[0] in "[{"

    def analyze(self, ctx: AnalysisContext) -> list[Finding]:
        findings: list[Finding] = []
        content_type = ctx.response.content_type or ""
        body = ctx.response.body
        if not body or not content_type:
            return findings

        text = ctx.response.text[:2048]

        if "json" in content_type:
            if len(body) > self._MAX_JSON_VALIDATE_SIZE:
                if not self._looks_like_json_prefix(text):
                    findings.append(
                        Finding(
                            category=self.category,
                            severity=Severity.WARNING,
                            title="Content-Type says JSON but body does not look like JSON",
                            description=f"Content-Type: {content_type}",
                            analyzer_id=self.analyzer_id,
                            recommendation="Return JSON payloads starting with an object/array, or correct the Content-Type header.",
                            details={"content_type": content_type, "body_size": len(body)},
                        ),
                    )
                    return findings
                findings.append(
                    Finding(
                        category=self.category,
                        severity=Severity.INFO,
                        title="Large JSON payload skipped strict validation",
                        description=(
                            f"Body size {len(body)} bytes exceeds validation limit "
                            f"{self._MAX_JSON_VALIDATE_SIZE} bytes."
                        ),
                        analyzer_id=self.analyzer_id,
                        recommendation="Use streaming JSON validation for large payloads in CI/contract tests.",
                        details={
                            "body_size": len(body),
                            "validation_limit": self._MAX_JSON_VALIDATE_SIZE,
                        },
                    ),
                )
                return findings
            parsed = ctx.response.json_safe()
            if parsed is None:
                findings.append(
                    Finding(
                        category=self.category,
                        severity=Severity.WARNING,
                        title="Content-Type says JSON but body is not valid JSON",
                        description=f"Content-Type: {content_type}",
                        analyzer_id=self.analyzer_id,
                        recommendation="Return valid JSON for JSON content types or correct the Content-Type header.",
                        details={"content_type": content_type},
                    ),
                )
        elif "xml" in content_type:
            stripped = text.lstrip()
            if stripped and not stripped.startswith("<"):
                findings.append(
                    Finding(
                        category=self.category,
                        severity=Severity.WARNING,
                        title="Content-Type says XML but body does not start with '<'",
                        description=f"Content-Type: {content_type}",
                        analyzer_id=self.analyzer_id,
                        recommendation="Ensure XML payloads are serialized correctly and set Content-Type accordingly.",
                        details={"content_type": content_type},
                    ),
                )
        elif "html" in content_type:
            stripped = text.lstrip().lower()
            if stripped and not (stripped.startswith("<") or stripped.startswith("<!doctype")):
                findings.append(
                    Finding(
                        category=self.category,
                        severity=Severity.INFO,
                        title="Content-Type says HTML but body does not look like HTML",
                        description=f"Content-Type: {content_type}",
                        analyzer_id=self.analyzer_id,
                        recommendation="Serve HTML with valid markup or switch to a more accurate Content-Type.",
                        details={"content_type": content_type},
                    ),
                )

        return findings


class DuplicateJsonKeysAnalyzer(Analyzer):  # type: ignore[misc]
    analyzer_id = "consistency.duplicate_keys"
    category = Category.CONSISTENCY
    display_name = "Duplicate JSON Keys"
    requires_valid_json_body = True

    _MAX_SCAN = 512_000

    def analyze(self, ctx: AnalysisContext) -> list[Finding]:
        findings: list[Finding] = []
        if not ctx.response.is_json or not ctx.response.body:
            return findings

        if len(ctx.response.body) > self._MAX_SCAN:
            findings.append(
                Finding(
                    category=self.category,
                    severity=Severity.INFO,
                    title="Duplicate-key scan skipped for large JSON body",
                    description=(
                        f"Body size {len(ctx.response.body)} bytes exceeds scan limit "
                        f"{self._MAX_SCAN} bytes."
                    ),
                    analyzer_id=self.analyzer_id,
                    recommendation="Run duplicate-key validation server-side or in CI for very large JSON responses.",
                    details={"body_size": len(ctx.response.body), "scan_limit": self._MAX_SCAN},
                ),
            )
            return findings

        text = ctx.response.text
        duplicate_keys: set[str] = set()

        def detect_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            seen: dict[str, int] = {}
            for key, _value in pairs:
                seen[key] = seen.get(key, 0) + 1
            for key, count in seen.items():
                if count > 1:
                    duplicate_keys.add(key)
            return dict(pairs)

        try:
            json.loads(text, object_pairs_hook=detect_duplicates)
        except Exception:
            logger.exception(
                "DuplicateJsonKeysAnalyzer: invalid JSON payload, skipping",
                exc_info=True,
            )
            return findings

        if duplicate_keys:
            keys_sorted = sorted(duplicate_keys)
            findings.append(
                Finding(
                    category=self.category,
                    severity=Severity.WARNING,
                    title=f"{len(keys_sorted)} duplicate JSON key(s)",
                    description=f"Duplicate keys: {', '.join(keys_sorted[:10])}. Behavior varies across parsers.",
                    analyzer_id=self.analyzer_id,
                    recommendation="Remove duplicate keys in serializers to avoid parser-dependent behavior.",
                    details={"duplicate_keys": keys_sorted[:20]},
                ),
            )
        return findings


class RedirectLocationAnalyzer(Analyzer):  # type: ignore[misc]
    analyzer_id = "consistency.redirect_location"
    category = Category.CONSISTENCY
    display_name = "Redirect Location Header"

    _REDIRECT_CODES = {301, 302, 303, 307, 308}

    def analyze(self, ctx: AnalysisContext) -> list[Finding]:
        findings: list[Finding] = []
        status_code = ctx.response.status_code
        if status_code not in self._REDIRECT_CODES:
            return findings

        location = (ctx.response.headers.get("location", "") or "").strip()
        if location:
            return findings

        findings.append(
            Finding(
                category=self.category,
                severity=Severity.WARNING,
                title=f"{status_code} redirect without Location header",
                description=f"Status {status_code} indicates a redirect but no Location header was returned.",
                analyzer_id=self.analyzer_id,
                recommendation="Include a valid Location header for redirect responses.",
                details={"status_code": status_code},
            ),
        )
        return findings


_DATE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("ISO 8601", re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")),
    ("ISO date only", re.compile(r'"\d{4}-\d{2}-\d{2}"')),
    ("Unix timestamp (s)", re.compile(r":\s*1[5-9]\d{8}(?:\.\d+)?\s*[,}\]]")),
    ("Unix timestamp (ms)", re.compile(r":\s*1[5-9]\d{11}\s*[,}\]]")),
    ("RFC 2822", re.compile(r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun),?\s+\d{1,2}\s+\w{3}\s+\d{4}")),
    ("US date (MM/DD/YYYY)", re.compile(r"\b\d{1,2}/\d{1,2}/\d{4}\b")),
]


class DateFormatInconsistencyAnalyzer(Analyzer):  # type: ignore[misc]
    analyzer_id = "consistency.date_formats"
    category = Category.CONSISTENCY
    display_name = "Date Format Inconsistency"

    _MAX_SCAN = 256_000

    def analyze(self, ctx: AnalysisContext) -> list[Finding]:
        findings: list[Finding] = []
        if not ctx.response.body:
            return findings

        text = ctx.response.text[: self._MAX_SCAN]
        formats_found: list[str] = []
        for label, pattern in _DATE_PATTERNS:
            if pattern.search(text):
                formats_found.append(label)

        if len(formats_found) >= 2:
            findings.append(
                Finding(
                    category=self.category,
                    severity=Severity.INFO,
                    title="Mixed date formats detected",
                    description=f"Found: {', '.join(formats_found)}. Consider standardizing on a single format (e.g. ISO 8601).",
                    analyzer_id=self.analyzer_id,
                    recommendation="Use one date format across the API, ideally ISO 8601 with timezone.",
                    details={"formats": formats_found},
                ),
            )
        return findings


class NullVsMissingAnalyzer(Analyzer):  # type: ignore[misc]
    analyzer_id = "consistency.null_vs_missing"
    category = Category.CONSISTENCY
    display_name = "Null vs Missing Field Patterns"
    requires_valid_json_body = True

    _CANDIDATE_LIST_KEYS = ("data", "results", "items", "records", "rows", "values")

    def analyze(self, ctx: AnalysisContext) -> list[Finding]:
        findings: list[Finding] = []
        if not ctx.response.is_json:
            return findings
        try:
            payload = ctx.response.json()
        except Exception:
            return findings

        items = payload if isinstance(payload, list) else self._extract_candidate_items(payload)
        if not isinstance(items, list) or len(items) < 2:
            return findings

        dict_items = [item for item in items[:200] if isinstance(item, dict)]
        if len(dict_items) < 2:
            return findings

        all_keys: set[str] = set()
        for item in dict_items:
            all_keys |= set(item.keys())

        inconsistent: list[str] = []
        for key in sorted(all_keys):
            has_null = False
            is_missing = False
            for item in dict_items:
                if key not in item:
                    is_missing = True
                elif item[key] is None:
                    has_null = True
            if has_null and is_missing:
                inconsistent.append(key)

        if inconsistent:
            findings.append(
                Finding(
                    category=self.category,
                    severity=Severity.INFO,
                    title=f"{len(inconsistent)} field(s) use both null and absent",
                    description=f"Fields: {', '.join(inconsistent[:10])}. Consider using one convention (null or omit).",
                    analyzer_id=self.analyzer_id,
                    recommendation="Pick one convention (null or omitted) for optional fields and document it.",
                    details={"fields": inconsistent[:20]},
                ),
            )

        return findings

    def _extract_candidate_items(self, payload: Any) -> list[Any]:
        if not isinstance(payload, dict):
            return []
        for key in self._CANDIDATE_LIST_KEYS:
            value = payload.get(key)
            if isinstance(value, list):
                return value
        return []


class SchemaDriftAnalyzer(Analyzer):  # type: ignore[misc]
    analyzer_id = "consistency.schema_drift"
    category = Category.CONSISTENCY
    display_name = "Schema Drift Detection"
    requires_valid_json_body = True

    _MAX_DEPTH = 8
    _MAX_LIST_SAMPLE = 5

    def analyze(self, ctx: AnalysisContext) -> list[Finding]:
        findings: list[Finding] = []
        if not ctx.response.is_json or ctx.stored_schema is None:
            return findings

        try:
            payload = ctx.response.json()
        except Exception:
            return findings

        current = self.build_schema_fingerprint(payload)
        stored = ctx.stored_schema

        added = set(current.keys()) - set(stored.keys())
        removed = set(stored.keys()) - set(current.keys())
        type_changed: list[str] = []
        for key in set(current.keys()) & set(stored.keys()):
            if current[key] != stored[key]:
                type_changed.append(f"{key}: {stored[key]} -> {current[key]}")

        if not added and not removed and not type_changed:
            return findings

        parts: list[str] = []
        if added:
            parts.append(f"Added: {', '.join(sorted(added)[:5])}")
        if removed:
            parts.append(f"Removed: {', '.join(sorted(removed)[:5])}")
        if type_changed:
            parts.append(f"Type changed: {', '.join(type_changed[:5])}")

        severity = Severity.WARNING if removed or type_changed else Severity.INFO
        findings.append(
            Finding(
                category=self.category,
                severity=severity,
                title="Response schema changed since last call",
                description=" | ".join(parts),
                analyzer_id=self.analyzer_id,
                recommendation="Version the contract or update clients/tests before deploying schema changes.",
                details={
                    "added": sorted(added)[:20],
                    "removed": sorted(removed)[:20],
                    "type_changed": type_changed[:20],
                },
            ),
        )
        return findings

    @staticmethod
    def build_schema_fingerprint(obj: Any, prefix: str = "", depth: int = 0) -> dict[str, str]:
        result: dict[str, str] = {}
        if depth > SchemaDriftAnalyzer._MAX_DEPTH:
            return result

        if isinstance(obj, dict):
            for key, value in obj.items():
                path = f"{prefix}.{key}" if prefix else str(key)
                result[path] = type(value).__name__
                if isinstance(value, (dict, list)):
                    result.update(
                        SchemaDriftAnalyzer.build_schema_fingerprint(value, path, depth + 1),
                    )
        elif isinstance(obj, list) and obj:
            list_path = f"{prefix}[]" if prefix else "[]"
            sample = obj[: SchemaDriftAnalyzer._MAX_LIST_SAMPLE]
            type_names = sorted({type(item).__name__ for item in sample})
            if type_names:
                result[list_path] = (
                    type_names[0] if len(type_names) == 1 else "mixed[" + "|".join(type_names) + "]"
                )
            for item in sample:
                if isinstance(item, (dict, list)):
                    result.update(
                        SchemaDriftAnalyzer.build_schema_fingerprint(item, list_path, depth + 1),
                    )

        return result
