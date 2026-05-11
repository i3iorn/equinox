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

_SENSITIVE_VALUE_PATTERNS: List[Pattern[str]] = [
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+\b"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9\-._~+/]+=*"),
]


class MissingSecurityHeadersAnalyzer(Analyzer):
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
    def _parse_hsts_max_age(value: str) -> Optional[int]:
        match = re.search(r"(?i)max-age\s*=\s*(\d+)", value or "")
        if not match:
            return None
        try:
            return int(match.group(1))
        except ValueError:
            return None

    def _invalid_security_header_issues(self, headers: Dict[str, str]) -> List[Dict[str, str]]:
        invalid: List[Dict[str, str]] = []

        hsts = headers.get("strict-transport-security", "")
        if hsts:
            max_age = self._parse_hsts_max_age(hsts)
            if max_age is None:
                invalid.append({"header": "strict-transport-security", "description": "HSTS missing max-age directive.", "severity": Severity.WARNING.value})
            elif max_age < 31536000:
                invalid.append({"header": "strict-transport-security", "description": "HSTS max-age is below recommended 31536000 seconds.", "severity": Severity.WARNING.value})

        csp = headers.get("content-security-policy", "")
        if csp:
            low = csp.lower()
            if "unsafe-inline" in low or "unsafe-eval" in low:
                invalid.append({"header": "content-security-policy", "description": "CSP includes unsafe directives (unsafe-inline or unsafe-eval).", "severity": Severity.WARNING.value})

        if headers.get("x-content-type-options", "") and headers.get("x-content-type-options", "").strip().lower() != "nosniff":
            invalid.append({"header": "x-content-type-options", "description": "X-Content-Type-Options should be 'nosniff'.", "severity": Severity.WARNING.value})

        if headers.get("x-frame-options", "") and headers.get("x-frame-options", "").strip().upper() not in ("DENY", "SAMEORIGIN"):
            invalid.append({"header": "x-frame-options", "description": "X-Frame-Options should be DENY or SAMEORIGIN.", "severity": Severity.INFO.value})

        referrer = headers.get("referrer-policy", "")
        if referrer:
            policy = referrer.split(",", 1)[0].strip().lower()
            if policy and policy not in _SAFE_REFERRER_POLICIES:
                invalid.append({"header": "referrer-policy", "description": f"Unrecognized Referrer-Policy value: {policy}", "severity": Severity.INFO.value})

        return invalid

    def analyze(self, ctx: AnalysisContext) -> List[Finding]:
        findings: List[Finding] = []
        missing: List[Dict[str, str]] = []
        invalid = self._invalid_security_header_issues(ctx.response.headers)

        for header, (description, severity) in self._EXPECTED.items():
            if header not in ctx.response.headers:
                missing.append({"header": header, "description": description, "severity": severity.value})

        if not missing and not invalid:
            return findings

        worst = Severity.INFO
        all_issues = [*missing, *invalid]
        if any(entry["severity"] == Severity.WARNING.value for entry in all_issues):
            worst = Severity.WARNING

        findings.append(Finding(
            category=self.category,
            severity=worst,
            title=f"{len(all_issues)} security header issue(s)",
            description="The response is missing or misconfigures recommended security headers.",
            analyzer_id=self.analyzer_id,
            recommendation="Add HSTS, CSP, and related browser hardening headers at your API gateway or app middleware.",
            details={"missing": missing, "invalid": invalid},
        ))
        return findings


class CookieFlagsAnalyzer(Analyzer):
    analyzer_id = "security.cookie_flags"
    category = Category.SECURITY
    display_name = "Cookie Security Flags"

    @staticmethod
    def _split_set_cookie_header(raw: str) -> List[str]:
        if not raw:
            return []
        parts: List[str] = []
        token: List[str] = []
        in_expires = False
        idx = 0
        while idx < len(raw):
            char = raw[idx]
            token.append(char)
            if raw[idx:idx + 8].lower() == "expires=":
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

    def analyze(self, ctx: AnalysisContext) -> List[Finding]:
        import http.cookies as cookies

        findings: List[Finding] = []
        raw_set_cookie = ctx.response.headers.get("set-cookie", "")
        if not raw_set_cookie:
            return findings

        cookies_raw = self._split_set_cookie_header(raw_set_cookie)
        issues: List[Dict[str, Any]] = []
        highest = Severity.INFO

        for cookie_entry in cookies_raw:
            jar = cookies.SimpleCookie()
            try:
                jar.load(cookie_entry)
            except Exception:
                logger.info("CookieFlagsAnalyzer: failed to parse Set-Cookie value", exc_info=True)
                continue

            for name, morsel in jar.items():
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
    for index, char in enumerate(digits):
        digit = int(char)
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
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

    window = body[max(0, start_idx - 48):min(len(body), end_idx + 48)].lower()
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


PatternSpec = Tuple[str, Pattern[str], Severity, Optional[Callable[[str], bool]]]

_PII_PATTERNS: List[PatternSpec] = [
    ("Email address", re.compile(r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b"), Severity.WARNING, None),
    ("Phone number", re.compile(r"\b(?:\+?\d{1,3}[\s.-]?)?(?:\(?\d{2,4}\)?[\s.-]?)?\d{3,4}[\s.-]?\d{4}\b"), Severity.INFO, None),
    ("Swedish/Finnish SSN", re.compile(r"\b(?:20|19)?\d{2}(?:1[0-2]|0[0-9])(?:3[01]|\d{2})[-+A]?\d{3}[0-9A-Y]\b"), Severity.CRITICAL, _luhn_valid),
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

    _MAX_SCAN_SIZE = 512_000

    def analyze(self, ctx: AnalysisContext) -> List[Finding]:
        findings: List[Finding] = []
        body = ctx.response.text[:self._MAX_SCAN_SIZE] if ctx.response.body else ""
        if not body:
            return findings

        detected: List[Dict[str, Any]] = []
        highest = Severity.INFO

        for label, pattern, severity, validator in _PII_PATTERNS:
            count = 0
            for match in pattern.finditer(body):
                matched_text = match.group(0)
                # When a pattern uses capture groups, group(0) keeps a stable validator input.
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
            detected.append({
                "type": "High entropy secret-like token",
                "count": entropy_hits,
                "severity": Severity.WARNING.value,
            })
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

        allow_origin = (ctx.response.headers.get("access-control-allow-origin", "") or "").strip()
        allow_credentials = (ctx.response.headers.get("access-control-allow-credentials", "") or "").strip()
        vary = (ctx.response.headers.get("vary", "") or "").lower()
        request_origin = (ctx.request.headers.get("origin", "") or "").strip()

        if not allow_origin:
            return findings

        issues: List[str] = []
        severity = Severity.WARNING

        if "," in allow_origin or " " in allow_origin:
            issues.append("Access-Control-Allow-Origin should be a single origin value, not a list.")

        if allow_origin == "*":
            issues.append("Access-Control-Allow-Origin is wildcard (*) - any origin can read the response.")
            if allow_credentials.lower() == "true":
                issues.append("Combined with Allow-Credentials: true this is a critical misconfiguration.")
                severity = Severity.CRITICAL

        if allow_origin.lower() == "null":
            issues.append("Access-Control-Allow-Origin is 'null' which can unintentionally trust sandboxed/file origins.")

        if allow_credentials.lower() == "true" and request_origin and allow_origin == request_origin and "origin" not in vary:
            issues.append("Credentialed CORS with reflected origin should include Vary: Origin to avoid cache poisoning.")

        if not issues:
            return findings

        findings.append(Finding(
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
        ))
        return findings


class JWTDecodeAnalyzer(Analyzer):
    analyzer_id = "security.jwt_decode"
    category = Category.SECURITY
    display_name = "JWT Decode & Expiry Check"

    _JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+")
    _SAFE_CLAIM_KEYS = {"iss", "aud", "exp", "iat", "nbf", "jti", "scope", "scp", "token_use", "azp", "kid"}

    def analyze(self, ctx: AnalysisContext) -> List[Finding]:
        findings: List[Finding] = []
        sources: List[Tuple[str, str]] = []

        if ctx.response.body:
            sources.append(("body", ctx.response.text[:256_000]))

        auth_header = ctx.response.headers.get("authorization", "")
        if auth_header:
            sources.append(("header", auth_header))

        try:
            if ctx.response.is_json:
                payload = ctx.response.json()
                if isinstance(payload, dict):
                    for key in ("access_token", "token", "id_token", "jwt"):
                        value = payload.get(key, "")
                        if isinstance(value, str) and value.startswith("eyJ"):
                            sources.append((f"field '{key}'", value))
        except Exception:
            logger.debug("JWTDecodeAnalyzer: failed JSON token extraction", exc_info=True)

        for source_label, text in sources:
            for match in self._JWT_RE.finditer(text):
                token = match.group(0)
                decoded_header, claims = self._decode_jwt(token)
                if claims is None:
                    continue

                details: Dict[str, Any] = {"source": source_label, "claims": claims}
                severity = Severity.INFO

                safe_claims = self._sanitize_claims(claims)
                details["claims"] = safe_claims
                details["claim_keys"] = sorted(list(claims.keys()))[:20]

                algorithm = str(decoded_header.get("alg", "")).strip().lower() if decoded_header else ""
                if algorithm == "none":
                    severity = Severity.CRITICAL
                    details["algorithm_none"] = True

                expiry = claims.get("exp")
                if isinstance(expiry, str) and expiry.isdigit():
                    expiry = int(expiry)

                if isinstance(expiry, (int, float)):
                    remaining = expiry - time.time()
                    details["expires_in_seconds"] = int(remaining)
                    if remaining <= 0:
                        details["expired"] = True
                        if severity != Severity.CRITICAL:
                            severity = Severity.WARNING
                    elif remaining < 300:
                        details["expiring_soon"] = True
                        if severity != Severity.CRITICAL:
                            severity = Severity.WARNING
                    elif remaining > 60 * 60 * 24 * 30:
                        details["long_lived"] = True
                        if severity == Severity.INFO:
                            severity = Severity.WARNING
                else:
                    details["missing_exp"] = True
                    if severity == Severity.INFO:
                        severity = Severity.WARNING

                findings.append(Finding(
                    category=self.category,
                    severity=severity,
                    title=f"JWT found in {source_label}",
                    description=self._summarize(claims, details),
                    analyzer_id=self.analyzer_id,
                    recommendation="Avoid returning tokens in response bodies when possible and enforce short token expiry windows.",
                    details=details,
                ))
                break

        return findings

    @staticmethod
    def _decode_jwt(token: str) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        try:
            parts = token.split(".")
            if len(parts) != 3:
                return None, None

            header_b64, payload_b64 = parts[0], parts[1]
            header_padding = 4 - len(header_b64) % 4
            if header_padding != 4:
                header_b64 += "=" * header_padding
            payload_padding = 4 - len(payload_b64) % 4
            if payload_padding != 4:
                payload_b64 += "=" * payload_padding

            header = json.loads(base64.urlsafe_b64decode(header_b64))
            payload = json.loads(base64.urlsafe_b64decode(payload_b64))
            if not isinstance(header, dict) or not isinstance(payload, dict):
                return None, None
            return header, payload
        except Exception:
            return None, None

    @classmethod
    def _sanitize_claims(cls, claims: Dict[str, Any]) -> Dict[str, Any]:
        sanitized: Dict[str, Any] = {}
        for key, value in claims.items():
            key_name = str(key)
            if key_name in cls._SAFE_CLAIM_KEYS:
                if isinstance(value, (str, int, float, bool)):
                    sanitized[key_name] = value
                elif isinstance(value, list):
                    sanitized[key_name] = [str(item) for item in value[:10]]
                else:
                    sanitized[key_name] = str(value)[:128]
        return sanitized

    @staticmethod
    def _summarize(claims: Dict[str, Any], details: Dict[str, Any]) -> str:
        parts: List[str] = []
        if claims.get("sub"):
            parts.append(f"Subject: {claims['sub']}")
        if claims.get("iss"):
            parts.append(f"Issuer: {claims['iss']}")

        if details.get("expired"):
            parts.append("Token is EXPIRED")
        elif details.get("expiring_soon"):
            parts.append(f"Token expires in {details['expires_in_seconds']}s")
        elif details.get("long_lived"):
            parts.append("Long-lived token")
        elif details.get("missing_exp"):
            parts.append("No exp claim")
        elif "expires_in_seconds" in details:
            parts.append(f"Expires in ~{details['expires_in_seconds'] // 60} min")

        if details.get("algorithm_none"):
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
        if _contains_sensitive_values(ctx.response.text[:256_000]):
            exposure_signals.append("sensitive value patterns in body")

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

        if not issues:
            return findings

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


def _contains_sensitive_values(body_text: str) -> bool:
    if not body_text:
        return False
    for pattern in _SENSITIVE_VALUE_PATTERNS:
        if pattern.search(body_text):
            return True
    for match in _HIGH_ENTROPY_RE.finditer(body_text):
        candidate = match.group(0)
        if _looks_like_high_entropy_secret(candidate, body_text, match.start(), match.end()):
            return True
    return False


