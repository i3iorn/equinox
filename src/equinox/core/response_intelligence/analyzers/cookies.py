"""Cookie security flags analyzer."""

import logging
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


class CookieFlagsAnalyzer(Analyzer):
    """Detects missing or incorrect cookie security flags."""

    analyzer_id = "security.cookie_flags"
    category = Category.SECURITY
    display_name = "Cookie Security Flags"

    @staticmethod
    def _split_set_cookie_header(raw: str) -> list[str]:
        """Parse Set-Cookie header containing multiple cookies.

        Handles comma-separated cookies while respecting expires date format
        which contains commas.
        """
        if not raw:
            return []
        parts: list[str] = []
        token: list[str] = []
        in_expires = False
        idx = 0
        while idx < len(raw):
            char = raw[idx]
            token.append(char)
            if raw[idx : idx + 8].lower() == "expires=":
                in_expires = True
            elif char == ";" and in_expires:
                in_expires = False
            elif char == "," and not in_expires:
                candidate = "".join(token[:-1]).strip()
                if candidate:
                    parts.append(candidate)
                token = []
                while idx + 1 < len(raw) and raw[idx + 1].isspace():
                    idx += 1
            idx += 1

        tail = "".join(token).strip()
        if tail:
            parts.append(tail)
        return parts

    def analyze(self, ctx: AnalysisContext) -> list[Finding]:
        """Analyze Set-Cookie headers for security flag issues."""
        import http.cookies as cookies

        findings: list[Finding] = []
        raw_set_cookie = ctx.response.headers.get("set-cookie", "")
        if not raw_set_cookie:
            return findings

        cookies_raw = self._split_set_cookie_header(raw_set_cookie)
        issues: list[dict[str, Any]] = []
        highest = Severity.INFO

        for cookie_entry in cookies_raw:
            jar = cookies.SimpleCookie()
            try:
                jar.load(cookie_entry)
            except Exception:
                logger.info("CookieFlagsAnalyzer: failed to parse Set-Cookie value", exc_info=True)
                continue

            for name, morsel in jar.items():
                problems: list[str] = []
                severity = Severity.INFO
                secure_set = bool(morsel["secure"])

                if not secure_set:
                    problems.append("Secure flag missing")
                    severity = Severity.WARNING
                if not morsel["httponly"]:
                    problems.append("HttpOnly flag missing")
                    severity = Severity.WARNING

                samesite = (morsel.get("samesite", "") or "").strip().lower()
                if not samesite:
                    problems.append("SameSite not set")
                    severity = Severity.WARNING
                elif samesite not in ("lax", "strict", "none"):
                    problems.append(f"SameSite has unsupported value: {samesite}")
                    severity = Severity.WARNING

                if samesite == "none" and not secure_set:
                    problems.append("SameSite=None requires Secure")
                    severity = Severity.CRITICAL

                if problems:
                    issues.append(
                        {"cookie": name, "problems": problems, "severity": severity.value},
                    )
                    if _SEVERITY_RANK[severity] > _SEVERITY_RANK[highest]:
                        highest = severity

        if issues:
            findings.append(
                Finding(
                    category=self.category,
                    severity=highest,
                    title=f"{len(issues)} cookie(s) missing security flags",
                    description="Cookies should use Secure, HttpOnly, and SameSite attributes.",
                    analyzer_id=self.analyzer_id,
                    recommendation="Set Secure, HttpOnly, and SameSite=Strict or Lax for all session cookies.",
                    details={"cookies": issues},
                ),
            )
        return findings
