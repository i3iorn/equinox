"""Security & Compliance analyzers."""

from __future__ import annotations

import base64
import json
import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional, Pattern, Set, Tuple

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


class MissingSecurityHeadersAnalyzer(Analyzer):
    analyzer_id = "security.missing_headers"
    category = Category.SECURITY
    display_name = "Missing Security Headers"

    # header -> (description, severity)
    _EXPECTED = {
        "strict-transport-security": (
            "HSTS not set - browser will accept plain HTTP connections.",
            Severity.WARNING,
        ),
        "content-security-policy": (
            "CSP not set - no defence against XSS / injection.",
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
        for entry in missing:
            if entry["severity"] == Severity.WARNING.value:
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
        issues: List[Dict[str, Any]] = []

        raw_cookie_value = ctx.response.headers.get("set-cookie", "")
        if not raw_cookie_value:
            return findings

        parsed = _hc.SimpleCookie()
        try:
            parsed.load(raw_cookie_value)
        except Exception:
            logger.debug("CookieFlagsAnalyzer: failed to parse Set-Cookie header", exc_info=True)
            return findings

        highest = Severity.INFO
        for name, morsel in parsed.items():
            problems: List[str] = []
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
                issues.append({"cookie": name, "problems": problems, "severity": severity.value})
                if _SEVERITY_RANK[severity] > _SEVERITY_RANK[highest]:
                    highest = severity

        if issues:
            findings.append(Finding(
                category=self.category,
                severity=highest,
                title=f"{len(issues)} cookie(s) missing security flags",
                description="Cookies should use Secure, HttpOnly, and SameSite attributes.",
                analyzer_id=self.analyzer_id,
                recommendation="Set Secure, HttpOnly, and SameSite=Strict or Lax for all session cookies.",
                details={"cookies": issues},
            ))
        return findings


def _luhn_valid(candidate: str) -> bool:
    digits = "".join(ch for ch in candidate if ch.isdigit())
    if len(digits) < 13 or len(digits) > 19:
        return False
    total = 0
    parity = len(digits) % 2
    for i, ch in enumerate(digits):
        d = int(ch)
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _looks_like_high_entropy_secret(candidate: str, body: str, start_idx: int, end_idx: int) -> bool:
    if len(candidate) < 24:
        return False
    charset_count = sum(
        bool(re.search(pattern, candidate))
        for pattern in (r"[a-z]", r"[A-Z]", r"\d", r"[_\-+=/]")
    )
    if charset_count < 3:
        return False

    # Reduce noise by requiring nearby secret-like context.
    window = body[max(0, start_idx - 48):min(len(body), end_idx + 48)].lower()
    context_markers = (
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
    return any(marker in window for marker in context_markers)


PatternSpec = Tuple[str, Pattern[str], Severity, Optional[Callable[[str], bool]]]

# Pre-compiled patterns for PII / secret detection
_PII_PATTERNS: List[PatternSpec] = [
    ("Email address", re.compile(r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b"), Severity.WARNING, None),
    ("Phone number", re.compile(r"\b(?:\+?\d{1,3}[\s.-]?)?(?:\(?\d{2,4}\)?[\s.-]?)?\d{3,4}[\s.-]?\d{4}\b"), Severity.INFO, None),
    ("Swedish/Finnish SSN", re.compile(r"\b(20|19)?\d{2}(1[0-2]|0[0-9])(3[01]|\d{2})[-+A]?\d{3}[0-9A-Y]\b"), Severity.CRITICAL, _luhn_valid),
    ("Norwegian SSN", re.compile(r"\b\d{6}[- ]?\d{5}\b"), Severity.CRITICAL, None),
    ("Danish CPR", re.compile(r"\b(?:0[1-9]|[12][0-9]|3[01])(?:0[1-9]|1[0-2])\d{2}-?\d{4}\b"), Severity.CRITICAL, None),
    ("US SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), Severity.CRITICAL, None),
    ("Credit card", re.compile(r"\b(?:\d[ -]*?){13,19}\b"), Severity.CRITICAL, _luhn_valid),
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA|AIDA|AROA)[0-9A-Z]{16}\b"), Severity.WARNING, None),
    ("AWS secret key", re.compile(r"(?i)aws[_\-]?secret[_\-]?access[_\-]?key[\s:=]+['\"]?([A-Za-z0-9/+=]{40})"), Severity.CRITICAL, None),
    ("GitHub token", re.compile(r"\bgh[opusr]_[A-Za-z0-9]{36,255}\b"), Severity.CRITICAL, None),
    ("Private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"), Severity.CRITICAL, None),
    ("Bearer token in body", re.compile(r"(?i)bearer\s+[A-Za-z0-9\-._~+/]+=*"), Severity.WARNING, None),
    ("JWT", re.compile(r"\beyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\b"), Severity.WARNING, None),
]

_HIGH_ENTROPY_RE = re.compile(r"\b[A-Za-z0-9_\-+/=]{24,}\b")


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

        detected: List[Dict[str, Any]] = []
        highest = Severity.INFO
        for label, pattern, severity, validator in _PII_PATTERNS:
            raw_matches = pattern.findall(body)
            matches = raw_matches
            if validator is not None:
                matches = [m for m in raw_matches if validator(m)]
            if matches:
                # Show count only, never echo the actual sensitive data.
                detected.append({"type": label, "count": len(matches), "severity": severity.value})
                if _SEVERITY_RANK[severity] > _SEVERITY_RANK[highest]:
                    highest = severity

        # High-entropy detector is intentionally context-gated to reduce noise.
        entropy_hits = 0
        for match in _HIGH_ENTROPY_RE.finditer(body):
            candidate = match.group(0)
            if _looks_like_high_entropy_secret(candidate, body, match.start(), match.end()):
                entropy_hits += 1
        if entropy_hits:
            detected.append({
                "type": "High entropy secret-like token",
                "count": entropy_hits,
                "severity": Severity.WARNING.value,
            })
            if _SEVERITY_RANK[Severity.WARNING] > _SEVERITY_RANK[highest]:
                highest = Severity.WARNING

        if detected:
            logger.warning(
                "PIILeakDetection: potential sensitive data found in response body: %s",
                [d["type"] for d in detected],
            )
            findings.append(Finding(
                category=self.category,
                severity=highest,
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
        acao = (ctx.response.headers.get("access-control-allow-origin", "") or "").strip()
        creds = (ctx.response.headers.get("access-control-allow-credentials", "") or "").strip()
        vary = (ctx.response.headers.get("vary", "") or "").lower()
        req_origin = (ctx.request.headers.get("origin", "") or "").strip()

        if not acao:
            return findings

        issues: List[str] = []
        severity = Severity.WARNING

        if "," in acao or " " in acao:
            issues.append("Access-Control-Allow-Origin should be a single origin value, not a list.")

        if acao == "*":
            issues.append("Access-Control-Allow-Origin is wildcard (*) - any origin can read the response.")
            if creds.lower() == "true":
                issues.append("Combined with Allow-Credentials: true this is a critical misconfiguration.")
                severity = Severity.CRITICAL

        if acao.lower() == "null":
            issues.append("Access-Control-Allow-Origin is 'null' which can unintentionally trust sandboxed/file origins.")

        if creds.lower() == "true" and req_origin and acao == req_origin and "origin" not in vary:
            issues.append("Credentialed CORS with reflected origin should include Vary: Origin to avoid cache poisoning.")

        if issues:
            logger.warning(
                "CORSMisconfigAnalyzer: overly permissive CORS - allow_origin=%r allow_credentials=%r",
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
                details={
                    "allow_origin": acao,
                    "allow_credentials": creds,
                    "request_origin": req_origin,
                    "vary": vary,
                },
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
        sources: List[Tuple[str, str]] = []
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
                    for key in ("access_token", "token", "id_token", "jwt"):
                        value = obj.get(key, "")
                        if isinstance(value, str) and value.startswith("eyJ"):
                            sources.append((f"field '{key}'", value))
        except Exception:
            logger.debug("JWTDecodeAnalyzer: failed JSON token extraction", exc_info=True)

        for source_label, text in sources:
            for match in self._JWT_RE.finditer(text):
                jwt_str = match.group(0)
                decoded_header, decoded_claims = self._decode_jwt(jwt_str)
                if decoded_claims is None:
                    continue

                detail: Dict[str, Any] = {"source": source_label, "claims": decoded_claims}
                exp = decoded_claims.get("exp")
                sev = Severity.INFO

                alg = str(decoded_header.get("alg", "")).strip().lower() if decoded_header else ""
                if alg == "none":
                    sev = Severity.CRITICAL
                    detail["algorithm_none"] = True

                if isinstance(exp, str) and exp.isdigit():
                    exp = int(exp)
                if isinstance(exp, (int, float)):
                    remaining = exp - time.time()
                    detail["expires_in_seconds"] = int(remaining)
                    if remaining <= 0:
                        sev = Severity.WARNING
                        detail["expired"] = True
                    elif remaining < 300:
                        sev = Severity.WARNING
                        detail["expiring_soon"] = True
                    elif remaining > 60 * 60 * 24 * 30:
                        detail["long_lived"] = True
                        if sev == Severity.INFO:
                            sev = Severity.WARNING
                else:
                    detail["missing_exp"] = True
                    if sev == Severity.INFO:
                        sev = Severity.WARNING

                findings.append(Finding(
                    category=self.category,
                    severity=sev,
                    title=f"JWT found in {source_label}",
                    description=self._summarise(decoded_claims, detail),
                    analyzer_id=self.analyzer_id,
                    recommendation="Avoid returning tokens in response bodies when possible and enforce short token expiry windows.",
                    details=detail,
                ))
                break  # one per source
        return findings

    # ------------------------------------------------------------------

    @staticmethod
    def _decode_jwt(token: str) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        try:
            parts = token.split(".")
            if len(parts) != 3:
                return None, None
            header_b64, payload_b64 = parts[0], parts[1]

            # Fix padding
            header_padding = 4 - len(header_b64) % 4
            if header_padding != 4:
                header_b64 += "=" * header_padding
            payload_padding = 4 - len(payload_b64) % 4
            if payload_padding != 4:
                payload_b64 += "=" * payload_padding

            header_bytes = base64.urlsafe_b64decode(header_b64)
            payload_bytes = base64.urlsafe_b64decode(payload_b64)
            header = json.loads(header_bytes)
            payload = json.loads(payload_bytes)
            if not isinstance(header, dict) or not isinstance(payload, dict):
                return None, None
            return header, payload
        except Exception:
            return None, None

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
            parts.append("Token is EXPIRED")
        elif detail.get("expiring_soon"):
            secs = detail["expires_in_seconds"]
            parts.append(f"Token expires in {secs}s")
        elif detail.get("long_lived"):
            parts.append("Long-lived token")
        elif detail.get("missing_exp"):
            parts.append("No exp claim")
        elif "expires_in_seconds" in detail:
            mins = detail["expires_in_seconds"] // 60
            parts.append(f"Expires in ~{mins} min")
        if detail.get("algorithm_none"):
            parts.append("Insecure alg=none")
        return " | ".join(parts) if parts else "JWT decoded successfully."


class SensitiveDataCachingAnalyzer(Analyzer):
    analyzer_id = "security.sensitive_cache"
    category = Category.SECURITY
    display_name = "Sensitive Data Caching"

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

    def analyze(self, ctx: AnalysisContext) -> List[Finding]:
        findings: List[Finding] = []
        cache_control = (ctx.response.headers.get("cache-control", "") or "").lower()
        pragma = (ctx.response.headers.get("pragma", "") or "").lower()

        exposure_signals: List[str] = []
        if ctx.response.headers.get("set-cookie"):
            exposure_signals.append("set-cookie")
        if ctx.response.headers.get("authorization"):
            exposure_signals.append("authorization header")

        parsed_json = ctx.response.json_safe()
        if _contains_sensitive_keys(parsed_json, self._SENSITIVE_KEYS):
            exposure_signals.append("sensitive fields in body")

        if not exposure_signals:
            return findings

        issues: List[str] = []
        severity = Severity.WARNING
        if "public" in cache_control:
            issues.append("Cache-Control includes 'public' for a response that appears to include sensitive data.")
            severity = Severity.CRITICAL
        if "no-store" not in cache_control:
            issues.append("Cache-Control does not include 'no-store' for sensitive response data.")
        if "no-cache" in pragma and "no-store" not in cache_control:
            issues.append("Pragma: no-cache is set but Cache-Control: no-store is missing.")

        if issues:
            findings.append(Finding(
                category=self.category,
                severity=severity,
                title="Sensitive response may be cacheable",
                description="\n".join(issues),
                analyzer_id=self.analyzer_id,
                recommendation="For sensitive responses, return Cache-Control: no-store (and avoid public caches).",
                details={
                    "signals": exposure_signals,
                    "cache_control": cache_control,
                    "pragma": pragma,
                },
            ))
        return findings


def _contains_sensitive_keys(value: Any, sensitive_keys: Set[str], depth: int = 0) -> bool:
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
