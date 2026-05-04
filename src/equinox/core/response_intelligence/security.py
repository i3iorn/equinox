"""Security & Compliance analyzers."""

from __future__ import annotations

import base64
import json
import logging
import re
import time
from typing import List

from equinox.core.response_intelligence.base import Analyzer
from equinox.core.response_intelligence.models import (
    AnalysisContext,
    Category,
    Finding,
    Severity,
)

logger = logging.getLogger(__name__)


class MissingSecurityHeadersAnalyzer(Analyzer):
    analyzer_id = "security.missing_headers"
    category = Category.SECURITY
    display_name = "Missing Security Headers"

    # header → (description, severity)
    _EXPECTED = {
        "strict-transport-security": (
            "HSTS not set — browser will accept plain HTTP connections.",
            Severity.WARNING,
        ),
        "content-security-policy": (
            "CSP not set — no defence against XSS / injection.",
            Severity.WARNING,
        ),
        "x-content-type-options": (
            "X-Content-Type-Options not set — browser may MIME-sniff responses.",
            Severity.INFO,
        ),
        "x-frame-options": (
            "X-Frame-Options not set — page can be embedded in iframes (click-jacking).",
            Severity.INFO,
        ),
        "permissions-policy": (
            "Permissions-Policy not set — browser features not explicitly restricted.",
            Severity.INFO,
        ),
        "referrer-policy": (
            "Referrer-Policy not set — full URL may leak in Referer header.",
            Severity.INFO,
        ),
    }

    def analyze(self, ctx: AnalysisContext) -> List[Finding]:
        findings: List[Finding] = []
        hdrs = ctx.response.headers
        missing = []
        for hdr, (desc, sev) in self._EXPECTED.items():
            if hdr not in hdrs:
                missing.append({"header": hdr, "description": desc, "severity": sev.value})

        if not missing:
            return findings

        worst = Severity.INFO
        for m in missing:
            if m["severity"] == "warning":
                worst = Severity.WARNING
                break

        findings.append(Finding(
            category=self.category,
            severity=worst,
            title=f"{len(missing)} security header(s) missing",
            description="The response is missing recommended security headers.",
            analyzer_id=self.analyzer_id,
            recommendation="Add HSTS, CSP, and related browser hardening headers at your API gateway or app middleware.",
            details={"missing": missing},
        ))
        return findings


class CookieFlagsAnalyzer(Analyzer):
    analyzer_id = "security.cookie_flags"
    category = Category.SECURITY
    display_name = "Cookie Security Flags"

    def analyze(self, ctx: AnalysisContext) -> List[Finding]:
        import http.cookies as _hc
        findings: List[Finding] = []
        issues: List[dict] = []

        for key, value in ctx.response.headers.items():
            if key.lower() != "set-cookie":
                continue
            try:
                m = _hc.SimpleCookie()
                m.load(value)
                for name, morsel in m.items():
                    problems = []
                    if not morsel["secure"]:
                        problems.append("Secure flag missing")
                    if not morsel["httponly"]:
                        problems.append("HttpOnly flag missing")
                    samesite = morsel.get("samesite", "")
                    if not samesite:
                        problems.append("SameSite not set")
                    if problems:
                        issues.append({"cookie": name, "problems": problems})
            except Exception:
                pass

        if issues:
            findings.append(Finding(
                category=self.category,
                severity=Severity.WARNING,
                title=f"{len(issues)} cookie(s) missing security flags",
                description="Cookies should use Secure, HttpOnly, and SameSite attributes.",
                analyzer_id=self.analyzer_id,
                recommendation="Set Secure, HttpOnly, and SameSite=Strict or Lax for all session cookies.",
                details={"cookies": issues},
            ))
        return findings


# Pre-compiled patterns for PII / secret detection
_PII_PATTERNS: List[tuple] = [
    ("Email address", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Z|a-z]{2,}\b")),
    ("Phone number", re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")),
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("Credit card", re.compile(r"\b(?:4\d{12}(?:\d{3})?|5[1-5]\d{14}|3[47]\d{13}|6(?:011|5\d{2})\d{12})\b")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("AWS secret key", re.compile(r"(?i)aws[_\-]?secret[_\-]?access[_\-]?key[\s:=]+['\"]?([A-Za-z0-9/+=]{40})")),
    ("Private key", re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----")),
    ("Bearer token in body", re.compile(r"(?i)bearer\s+[A-Za-z0-9\-._~+/]+=*")),
]


class PIILeakDetectionAnalyzer(Analyzer):
    analyzer_id = "security.pii_leak"
    category = Category.SECURITY
    display_name = "PII / Secret Leak Detection"

    # Only scan bodies up to this size to avoid perf issues
    _MAX_SCAN_SIZE = 512_000

    def analyze(self, ctx: AnalysisContext) -> List[Finding]:
        findings: List[Finding] = []
        body = ctx.response.text[:self._MAX_SCAN_SIZE] if ctx.response.body else ""
        if not body:
            return findings

        detected: List[dict] = []
        for label, pattern in _PII_PATTERNS:
            matches = pattern.findall(body)
            if matches:
                # Show count only, never echo the actual sensitive data
                detected.append({"type": label, "count": len(matches)})

        if detected:
            sev = Severity.CRITICAL if any(
                d["type"] in ("SSN", "Credit card", "Private key", "AWS secret key")
                for d in detected
            ) else Severity.WARNING
            logger.warning(
                "PIILeakDetection: potential sensitive data found in response body: %s",
                [d["type"] for d in detected],
            )
            findings.append(Finding(
                category=self.category,
                severity=sev,
                title="Potential sensitive data in response body",
                description="The response body contains patterns that may be PII or secrets.",
                analyzer_id=self.analyzer_id,
                recommendation="Remove or mask sensitive fields in API responses and rotate any exposed credentials.",
                details={"detected": detected},
            ))
        return findings


class CORSMisconfigAnalyzer(Analyzer):
    analyzer_id = "security.cors_misconfig"
    category = Category.SECURITY
    display_name = "CORS Misconfiguration"

    def analyze(self, ctx: AnalysisContext) -> List[Finding]:
        findings: List[Finding] = []
        acao = ctx.response.headers.get("access-control-allow-origin", "")
        creds = ctx.response.headers.get("access-control-allow-credentials", "")
        if not acao:
            return findings

        issues: List[str] = []
        severity = Severity.WARNING
        if acao == "*":
            issues.append("Access-Control-Allow-Origin is wildcard (*) — any origin can read the response.")
            if creds.lower() == "true":
                issues.append("Combined with Allow-Credentials: true this is a critical misconfiguration.")
                severity = Severity.CRITICAL

        if issues:
            logger.warning(
                "CORSMisconfigAnalyzer: overly permissive CORS — allow_origin=%r allow_credentials=%r",
                acao,
                ctx.response.headers.get("access-control-allow-credentials", ""),
            )
            findings.append(Finding(
                category=self.category,
                severity=severity,
                title="Overly permissive CORS policy",
                description="\n".join(issues),
                analyzer_id=self.analyzer_id,
                recommendation="Restrict Access-Control-Allow-Origin to trusted origins and avoid credentials with wildcard origins.",
                details={"allow_origin": acao, "allow_credentials": creds},
            ))
        return findings


class JWTDecodeAnalyzer(Analyzer):
    analyzer_id = "security.jwt_decode"
    category = Category.SECURITY
    display_name = "JWT Decode & Expiry Check"

    _JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+")

    def analyze(self, ctx: AnalysisContext) -> List[Finding]:
        findings: List[Finding] = []
        # Search in body and Authorization header
        sources = []
        if ctx.response.body:
            sources.append(("body", ctx.response.text[:256_000]))
        auth_hdr = ctx.response.headers.get("authorization", "")
        if auth_hdr:
            sources.append(("header", auth_hdr))
        # Also check common JSON fields
        try:
            if ctx.response.is_json:
                obj = ctx.response.json()
                if isinstance(obj, dict):
                    for k in ("access_token", "token", "id_token", "jwt"):
                        v = obj.get(k, "")
                        if isinstance(v, str) and v.startswith("eyJ"):
                            sources.append((f"field '{k}'", v))
        except Exception:
            pass

        for source_label, text in sources:
            for m in self._JWT_RE.finditer(text):
                jwt_str = m.group(0)
                decoded = self._decode_jwt(jwt_str)
                if decoded is None:
                    continue
                detail: dict = {"source": source_label, "claims": decoded}
                exp = decoded.get("exp")
                sev = Severity.INFO
                if isinstance(exp, (int, float)):
                    remaining = exp - time.time()
                    detail["expires_in_seconds"] = int(remaining)
                    if remaining <= 0:
                        sev = Severity.WARNING
                        detail["expired"] = True
                    elif remaining < 300:
                        sev = Severity.WARNING
                        detail["expiring_soon"] = True
                findings.append(Finding(
                    category=self.category,
                    severity=sev,
                    title=f"JWT found in {source_label}",
                    description=self._summarise(decoded, detail),
                    analyzer_id=self.analyzer_id,
                    recommendation="Avoid returning tokens in response bodies when possible and enforce short token expiry windows.",
                    details=detail,
                ))
                break  # one per source
        return findings

    # ------------------------------------------------------------------

    @staticmethod
    def _decode_jwt(token: str) -> "dict | None":
        try:
            payload_b64 = token.split(".")[1]
            # Fix padding
            padding = 4 - len(payload_b64) % 4
            if padding != 4:
                payload_b64 += "=" * padding
            payload_bytes = base64.urlsafe_b64decode(payload_b64)
            return json.loads(payload_bytes)
        except Exception:
            return None

    @staticmethod
    def _summarise(claims: dict, detail: dict) -> str:
        parts: List[str] = []
        sub = claims.get("sub")
        if sub:
            parts.append(f"Subject: {sub}")
        iss = claims.get("iss")
        if iss:
            parts.append(f"Issuer: {iss}")
        if detail.get("expired"):
            parts.append("⚠ Token is EXPIRED")
        elif detail.get("expiring_soon"):
            secs = detail["expires_in_seconds"]
            parts.append(f"⚠ Token expires in {secs}s")
        elif "expires_in_seconds" in detail:
            mins = detail["expires_in_seconds"] // 60
            parts.append(f"Expires in ~{mins} min")
        return " · ".join(parts) if parts else "JWT decoded successfully."

