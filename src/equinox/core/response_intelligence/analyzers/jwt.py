"""JWT decoding and expiry validation analyzer."""
import base64
import json
import logging
import re
import time
from typing import Any

from equinox.core.response_intelligence.base import Analyzer
from equinox.core.response_intelligence.models import AnalysisContext
from equinox.core.response_intelligence.models import Category
from equinox.core.response_intelligence.models import Finding
from equinox.core.response_intelligence.models import Severity

logger = logging.getLogger(__name__)

_SEVERITY_RANK = {
    Severity.INFO: 0,
    Severity.WARNING: 1,
    Severity.CRITICAL: 2,
}


class JWTDecodeAnalyzer(Analyzer):  # type: ignore[misc]
    """Decodes JWTs found in responses and checks for security issues."""

    analyzer_id = "security.jwt_decode"
    category = Category.SECURITY
    display_name = "JWT Decode & Expiry Check"

    _JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+")
    _SAFE_CLAIM_KEYS = {
        "iss",
        "aud",
        "exp",
        "iat",
        "nbf",
        "jti",
        "scope",
        "scp",
        "token_use",
        "azp",
        "kid",
    }

    def analyze(self, ctx: AnalysisContext) -> list[Finding]:
        """Analyze response for JWT tokens and validate them."""
        findings: list[Finding] = []
        sources: list[tuple[str, str]] = []

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
            logger.exception("JWTDecodeAnalyzer: failed JSON token extraction", exc_info=True)

        for source_label, text in sources:
            for match in self._JWT_RE.finditer(text):
                token = match.group(0)
                decoded_header, claims = self._decode_jwt(token)
                if claims is None:
                    continue

                details: dict[str, Any] = {"source": source_label, "claims": claims}
                severity = Severity.INFO

                safe_claims = self._sanitize_claims(claims)
                details["claims"] = safe_claims
                details["claim_keys"] = sorted(list(claims.keys()))[:20]

                algorithm = (
                    str(decoded_header.get("alg", "")).strip().lower() if decoded_header else ""
                )
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

                findings.append(
                    Finding(
                        category=self.category,
                        severity=severity,
                        title=f"JWT found in {source_label}",
                        description=self._summarize(claims, details),
                        analyzer_id=self.analyzer_id,
                        recommendation="Avoid returning tokens in response bodies when possible and enforce short token expiry windows.",
                        details=details,
                    ),
                )
                break

        return findings

    @staticmethod
    def _decode_jwt(token: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """Decode JWT header and payload without verification."""
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
    def _sanitize_claims(cls, claims: dict[str, Any]) -> dict[str, Any]:
        """Extract only safe claims to display."""
        sanitized: dict[str, Any] = {}
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
    def _summarize(claims: dict[str, Any], details: dict[str, Any]) -> str:
        """Build a human-readable summary of JWT details."""
        parts: list[str] = []
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
