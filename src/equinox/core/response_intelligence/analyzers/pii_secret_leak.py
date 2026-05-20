"""PII and secret leak detection analyzer."""

import logging
import re
from re import Pattern
from typing import Any

from equinox.core.response_intelligence.base import Analyzer
from equinox.core.response_intelligence.models import (
    AnalysisContext,
    Category,
    Finding,
    Severity,
)

logger = logging.getLogger(__name__)

_SEVERITY_RANK = {
    Severity.INFO: 0,
    Severity.WARNING: 1,
    Severity.CRITICAL: 2,
}


def _luhn_valid(candidate: str) -> bool:
    """Validate credit card number using Luhn algorithm."""
    digits = "".join(ch for ch in candidate if ch.isdigit())
    if len(digits) < 13 or len(digits) > 19:
        return False

    total = 0
    parity = len(digits) % 2
    for index, char in enumerate(digits):
        digit = int(char)
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def _looks_like_high_entropy_secret(
    candidate: str, body: str, start_idx: int, end_idx: int
) -> bool:
    """Check if a token looks like a high-entropy secret."""
    if len(candidate) < 24:
        return False

    charset_count = sum(
        bool(re.search(pattern, candidate)) for pattern in (r"[a-z]", r"[A-Z]", r"\d", r"[_\-+=/]")
    )
    if charset_count < 3:
        return False

    window = body[max(0, start_idx - 48) : min(len(body), end_idx + 48)].lower()
    markers = (
        "token",
        "secret",
        "password",
        "passwd",
        "api_key",
        "apikey",
        "client_secret",
        "bearer",
        "authorization",
        "session",
    )
    return any(marker in window for marker in markers)


def _contains_sensitive_keys(value: Any, sensitive_keys: set[str], depth: int = 0) -> bool:
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


def _contains_sensitive_values(body_text: str, patterns: list[Pattern[str]]) -> bool:
    """Check if body contains sensitive value patterns."""
    if not body_text:
        return False
    for pattern in patterns:
        if pattern.search(body_text):
            return True
    return False


PatternSpec = tuple[str, Pattern[str], Severity, callable] | None

_PII_PATTERNS: list[PatternSpec] = [
    (
        "Email address",
        re.compile(r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b"),
        Severity.WARNING,
        None,
    ),
    (
        "Phone number",
        re.compile(r"\b(?:\+?\d{1,3}[\s.-]?)?(?:\(?\d{2,4}\)?[\s.-]?)?\d{3,4}[\s.-]?\d{4}\b"),
        Severity.INFO,
        None,
    ),
    (
        "Swedish/Finnish SSN",
        re.compile(r"\b(?:20|19)?\d{2}(?:1[0-2]|0[0-9])(?:3[01]|\d{2})[-+A]?\d{3}[0-9A-Y]\b"),
        Severity.WARNING,
        _luhn_valid,
    ),
    ("Norwegian SSN", re.compile(r"\b\d{6}[- ]?\d{5}\b"), Severity.WARNING, None),
    (
        "Danish CPR",
        re.compile(r"\b(?:0[1-9]|[12][0-9]|3[01])(?:0[1-9]|1[0-2])\d{2}-?\d{4}\b"),
        Severity.WARNING,
        None,
    ),
    ("US SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), Severity.CRITICAL, None),
    ("Credit card", re.compile(r"\b(?:\d[ -]*?){13,19}\b"), Severity.CRITICAL, _luhn_valid),
    (
        "AWS access key",
        re.compile(r"\b(?:AKIA|ASIA|AIDA|AROA)[0-9A-Z]{16}\b"),
        Severity.WARNING,
        None,
    ),
    (
        "AWS secret key",
        re.compile(r"(?i)aws[_\-]?secret[_\-]?access[_\-]?key[\s:=]+['\"]?([A-Za-z0-9/+=]{40})"),
        Severity.CRITICAL,
        None,
    ),
    ("GitHub token", re.compile(r"\bgh[opusr]_[A-Za-z0-9]{36,255}\b"), Severity.CRITICAL, None),
    (
        "Private key",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
        Severity.CRITICAL,
        None,
    ),
    (
        "Bearer token in body",
        re.compile(r"(?i)bearer\s+[A-Za-z0-9\-._~+/]+=*"),
        Severity.WARNING,
        None,
    ),
    (
        "JWT",
        re.compile(r"\beyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\b"),
        Severity.WARNING,
        None,
    ),
]

_HIGH_ENTROPY_RE = re.compile(r"\b[A-Za-z0-9_\-+/=]{24,}\b")
_SENSITIVE_VALUE_PATTERNS: list[Pattern[str]] = [
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+\b"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9\-._~+/]+=*"),
]


class PIILeakDetectionAnalyzer(Analyzer):
    """Detects PII and secrets in response bodies."""

    analyzer_id = "security.pii_leak"
    category = Category.SECURITY
    display_name = "PII / Secret Leak Detection"

    _MAX_SCAN_SIZE = 512_000
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

    def analyze(self, ctx: AnalysisContext) -> list[Finding]:
        """Analyze response body for PII and secret patterns."""
        findings: list[Finding] = []
        body = ctx.response.text[: self._MAX_SCAN_SIZE] if ctx.response.body else ""
        if not body:
            return findings

        detected: list[dict[str, Any]] = []
        highest = Severity.INFO

        for label, pattern, severity, validator in _PII_PATTERNS:
            count = 0
            for match in pattern.finditer(body):
                matched_text = match.group(0)
                if validator is not None and not validator(matched_text):
                    continue
                count += 1

            if count:
                detected.append({"type": label, "count": count, "severity": severity.value})
                if _SEVERITY_RANK[severity] > _SEVERITY_RANK[highest]:
                    highest = severity

        entropy_hits = 0
        for match in _HIGH_ENTROPY_RE.finditer(body):
            candidate = match.group(0)
            if _looks_like_high_entropy_secret(candidate, body, match.start(), match.end()):
                entropy_hits += 1

        if entropy_hits:
            detected.append(
                {
                    "type": "High entropy secret-like token",
                    "count": entropy_hits,
                    "severity": Severity.WARNING.value,
                }
            )
            if _SEVERITY_RANK[Severity.WARNING] > _SEVERITY_RANK[highest]:
                highest = Severity.WARNING

        if not detected:
            return findings

        try:
            logger_at_level = getattr(logger, highest.name.lower())
        except AttributeError:
            logger_at_level = logger.warning

        logger_at_level(
            "PIILeakDetection: potential sensitive data found in response body: %s",
            [entry["type"] for entry in detected],
        )

        findings.append(
            Finding(
                category=self.category,
                severity=highest,
                title="Potential sensitive data in response body",
                description="The response body contains patterns that may be PII or secrets.",
                analyzer_id=self.analyzer_id,
                recommendation="Remove or mask sensitive fields in API responses and rotate any exposed credentials.",
                details={"detected": detected},
            )
        )
        return findings
