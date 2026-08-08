"""Security headers analyzer."""

import logging
import re

from equinox.core.response_intelligence.base import Analyzer
from equinox.core.response_intelligence.models import (
    AnalysisContext,
    Category,
    Finding,
    Severity,
)

logger = logging.getLogger(__name__)

_SAFE_REFERRER_POLICIES = {
    "no-referrer",
    "strict-origin",
    "strict-origin-when-cross-origin",
    "same-origin",
    "origin",
    "origin-when-cross-origin",
    "no-referrer-when-downgrade",
    "unsafe-url",
}


class MissingSecurityHeadersAnalyzer(Analyzer):
    """Detects missing or misconfigured security headers."""

    analyzer_id = "security.missing_headers"
    category = Category.SECURITY
    display_name = "Missing Security Headers"

    _EXPECTED = {
        "strict-transport-security": (
            "HSTS not set - browser will accept plain HTTP connections.",
            Severity.WARNING,
        ),
        "content-security-policy": (
            "CSP not set - no defense against XSS / injection.",
            Severity.WARNING,
        ),
        "x-content-type-options": (
            "X-Content-Type-Options not set - browser may MIME-sniff responses.",
            Severity.INFO,
        ),
        "x-frame-options": (
            "X-Frame-Options not set - page can be embedded in iframes (click-jacking).",
            Severity.INFO,
        ),
        "permissions-policy": (
            "Permissions-Policy not set - browser features not explicitly restricted.",
            Severity.INFO,
        ),
        "referrer-policy": (
            "Referrer-Policy not set - full URL may leak in Referer header.",
            Severity.INFO,
        ),
    }

    @staticmethod
    def _parse_hsts_max_age(value: str) -> int | None:
        """Extract max-age value from HSTS header."""
        match = re.search(r"(?i)max-age\s*=\s*(\d+)", value or "")
        if not match:
            return None
        try:
            return int(match.group(1))
        except ValueError:
            return None

    def _invalid_security_header_issues(self, headers: dict[str, str]) -> list[dict[str, str]]:
        """Check for invalid or misconfigured security headers."""
        invalid: list[dict[str, str]] = []

        hsts = headers.get("strict-transport-security", "")
        if hsts:
            max_age = self._parse_hsts_max_age(hsts)
            if max_age is None:
                invalid.append(
                    {
                        "header": "strict-transport-security",
                        "description": "HSTS missing max-age directive.",
                        "severity": Severity.WARNING.value,
                    },
                )
            elif max_age < 31536000:
                invalid.append(
                    {
                        "header": "strict-transport-security",
                        "description": "HSTS max-age is below recommended 31536000 seconds.",
                        "severity": Severity.WARNING.value,
                    },
                )

        csp = headers.get("content-security-policy", "")
        if csp:
            low = csp.lower()
            if "unsafe-inline" in low or "unsafe-eval" in low:
                invalid.append(
                    {
                        "header": "content-security-policy",
                        "description": "CSP includes unsafe directives (unsafe-inline or unsafe-eval).",
                        "severity": Severity.WARNING.value,
                    },
                )

        x_content_type = headers.get("x-content-type-options", "")
        if x_content_type and x_content_type.strip().lower() != "nosniff":
            invalid.append(
                {
                    "header": "x-content-type-options",
                    "description": "X-Content-Type-Options should be 'nosniff'.",
                    "severity": Severity.WARNING.value,
                },
            )

        x_frame_options = headers.get("x-frame-options", "")
        if x_frame_options and x_frame_options.strip().upper() not in ("DENY", "SAMEORIGIN"):
            invalid.append(
                {
                    "header": "x-frame-options",
                    "description": "X-Frame-Options should be DENY or SAMEORIGIN.",
                    "severity": Severity.INFO.value,
                },
            )

        referrer = headers.get("referrer-policy", "")
        if referrer:
            policy = referrer.split(",", 1)[0].strip().lower()
            if policy and policy not in _SAFE_REFERRER_POLICIES:
                invalid.append(
                    {
                        "header": "referrer-policy",
                        "description": f"Unrecognized Referrer-Policy value: {policy}",
                        "severity": Severity.INFO.value,
                    },
                )

        return invalid

    def analyze(self, ctx: AnalysisContext) -> list[Finding]:
        """Analyze response for missing or misconfigured security headers."""
        findings: list[Finding] = []
        missing: list[dict[str, str]] = []
        invalid = self._invalid_security_header_issues(ctx.response.headers)

        for header, (description, severity) in self._EXPECTED.items():
            if header not in ctx.response.headers:
                missing.append(
                    {
                        "header": header,
                        "description": description,
                        "severity": severity.value,
                    },
                )

        if not missing and not invalid:
            return findings

        worst = Severity.INFO
        all_issues = [*missing, *invalid]
        if any(entry["severity"] == Severity.WARNING.value for entry in all_issues):
            worst = Severity.WARNING

        findings.append(
            Finding(
                category=self.category,
                severity=worst,
                title=f"{len(all_issues)} security header issue(s)",
                description="The response is missing or misconfigures recommended security headers.",
                analyzer_id=self.analyzer_id,
                recommendation="Add HSTS, CSP, and related browser hardening headers at your API gateway or app middleware.",
                details={"missing": missing, "invalid": invalid},
            ),
        )
        return findings
