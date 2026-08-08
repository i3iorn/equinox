"""CORS misconfiguration analyzer."""

import logging

from equinox.core.response_intelligence.base import Analyzer
from equinox.core.response_intelligence.models import (
    AnalysisContext,
    Category,
    Finding,
    Severity,
)

logger = logging.getLogger(__name__)


class CORSMisconfigAnalyzer(Analyzer):
    """Detects CORS misconfigurations and security issues."""

    analyzer_id = "security.cors_misconfig"
    category = Category.SECURITY
    display_name = "CORS Misconfiguration"

    def analyze(self, ctx: AnalysisContext) -> list[Finding]:
        """Analyze CORS headers for security issues."""
        findings: list[Finding] = []

        allow_origin = (ctx.response.headers.get("access-control-allow-origin", "") or "").strip()
        allow_credentials = (
            ctx.response.headers.get("access-control-allow-credentials", "") or ""
        ).strip()
        vary = (ctx.response.headers.get("vary", "") or "").lower()
        request_origin = (ctx.request.headers.get("origin", "") or "").strip()

        if not allow_origin:
            return findings

        issues: list[str] = []
        severity = Severity.WARNING

        if "," in allow_origin or " " in allow_origin:
            issues.append(
                "Access-Control-Allow-Origin should be a single origin value, not a list.",
            )

        if allow_origin == "*":
            issues.append(
                "Access-Control-Allow-Origin is wildcard (*) - any origin can read the response.",
            )
            if allow_credentials.lower() == "true":
                issues.append(
                    "Combined with Allow-Credentials: true this is a critical misconfiguration.",
                )
                severity = Severity.CRITICAL

        if allow_origin.lower() == "null":
            issues.append(
                "Access-Control-Allow-Origin is 'null' which can unintentionally trust sandboxed/file origins.",
            )

        if (
            allow_credentials.lower() == "true"
            and request_origin
            and allow_origin == request_origin
            and "origin" not in vary
        ):
            issues.append(
                "Credentialed CORS with reflected origin should include Vary: Origin to avoid cache poisoning.",
            )

        if not issues:
            return findings

        findings.append(
            Finding(
                category=self.category,
                severity=severity,
                title="Overly permissive CORS policy",
                description="\n".join(issues),
                analyzer_id=self.analyzer_id,
                recommendation="Restrict Access-Control-Allow-Origin to trusted origins and avoid credentials with wildcard origins.",
                details={
                    "allow_origin": allow_origin,
                    "allow_credentials": allow_credentials,
                    "request_origin": request_origin,
                    "vary": vary,
                },
            ),
        )
        return findings
