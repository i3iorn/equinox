"""AWS Signature Version 4 authentication.

Pure-Python implementation using only the standard library (``hmac``,
``hashlib``, ``datetime``, ``urllib.parse``).  No ``boto3`` or ``botocore``
dependency required.

Reference:
    https://docs.aws.amazon.com/general/latest/gr/sigv4-create-canonical-request.html
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple
from urllib.parse import quote, urlparse, parse_qsl

from equinox.auth.base import AuthStrategy, _validate_credential
from equinox.core.exceptions import AuthError


class AWSSigV4Auth(AuthStrategy):
    """AWS Signature Version 4 request signing.

    Args:
        access_key:    AWS access key ID.
        secret_key:    AWS secret access key.
        region:        AWS region (e.g. ``"us-east-1"``).
        service:       AWS service identifier (e.g. ``"s3"``, ``"execute-api"``).
        session_token: Optional STS session token for temporary credentials.
    """

    AUTH_TYPE = "aws_sigv4"

    def __init__(
        self,
        access_key: str,
        secret_key: str,
        region: str,
        service: str,
        session_token: Optional[str] = None,
    ) -> None:
        # Validate all fields for type, length, and CRLF injection.
        # access_key / secret_key / session_token are injected into HTTP
        # headers — CRLF would allow header-injection attacks.
        self.access_key = _validate_credential(access_key, "AWS access_key")
        self.secret_key = _validate_credential(secret_key, "AWS secret_key")
        self.region = _validate_credential(region, "AWS region")
        self.service = _validate_credential(service, "AWS service")
        self.session_token: Optional[str] = (
            _validate_credential(session_token, "AWS session_token")
            if session_token
            else None
        )

    # ── AuthStrategy interface ────────────────────────────────────────────────

    def apply(self, request: Any, headers: Dict[str, str]) -> None:
        """Compute SigV4 signature and inject signing headers.

        Sets:
            ``Authorization``        — AWS4-HMAC-SHA256 Credential=…
            ``x-amz-date``           — ISO-8601 date-time (UTC)
            ``x-amz-security-token`` — (only when session_token is set)
        """
        now = datetime.now(timezone.utc)
        amzdate = now.strftime("%Y%m%dT%H%M%SZ")
        datestamp = now.strftime("%Y%m%d")

        headers["x-amz-date"] = amzdate
        if self.session_token:
            headers["x-amz-security-token"] = self.session_token

        url = getattr(request, "url", "") or ""
        method = (getattr(request, "method", "GET") or "GET").upper()
        body = getattr(request, "body", None) or ""
        body_bytes = body.encode("utf-8") if isinstance(body, str) else body

        parsed = urlparse(url)
        canonical_uri = self._canonical_uri(parsed.path)
        canonical_qs = self._canonical_query_string(parsed.query)

        host = parsed.hostname or ""
        if parsed.port and parsed.port not in (80, 443):
            host = f"{host}:{parsed.port}"
        headers["host"] = host  # lowercase — required by SigV4

        signed_headers, canonical_headers = self._canonical_headers(headers)

        payload_hash = hashlib.sha256(body_bytes).hexdigest()
        headers["x-amz-content-sha256"] = payload_hash

        canonical_request = "\n".join([
            method,
            canonical_uri,
            canonical_qs,
            canonical_headers,
            signed_headers,
            payload_hash,
        ])

        credential_scope = "/".join([datestamp, self.region, self.service, "aws4_request"])
        string_to_sign = "\n".join([
            "AWS4-HMAC-SHA256",
            amzdate,
            credential_scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ])

        signing_key = self._get_signing_key(datestamp)
        signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

        headers["Authorization"] = (
            f"AWS4-HMAC-SHA256 Credential={self.access_key}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, "
            f"Signature={signature}"
        )

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "type":       self.AUTH_TYPE,
            "access_key": self.access_key,
            "secret_key": self.secret_key,
            "region":     self.region,
            "service":    self.service,
        }
        if self.session_token:
            d["session_token"] = self.session_token
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AWSSigV4Auth":
        """Create from dictionary.

        Raises:
            AuthError: If required keys are missing.
        """
        try:
            return cls(
                access_key=data["access_key"],
                secret_key=data["secret_key"],
                region=data["region"],
                service=data["service"],
                session_token=data.get("session_token"),
            )
        except KeyError as exc:
            raise AuthError(f"Invalid AWS SigV4 auth data: missing key {exc}") from exc

    def __repr__(self) -> str:
        masked_key = (
            f"{self.access_key[:4]}****" if len(self.access_key) > 4 else "****"
        )
        return (
            f"AWSSigV4Auth(access_key={masked_key!r}, "
            f"region={self.region!r}, service={self.service!r})"
        )

    # ── SigV4 implementation helpers ──────────────────────────────────────────

    def _get_signing_key(self, datestamp: str) -> bytes:
        k_date = self._sign(("AWS4" + self.secret_key).encode("utf-8"), datestamp)
        k_region = self._sign(k_date, self.region)
        k_service = self._sign(k_region, self.service)
        k_signing = self._sign(k_service, "aws4_request")
        return k_signing

    @staticmethod
    def _sign(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

    @staticmethod
    def _canonical_uri(path: str) -> str:
        if not path:
            return "/"
        # Encode each segment (preserve slashes, double-encode existing %XX)
        segments = path.split("/")
        return "/".join(quote(s, safe="") for s in segments) or "/"

    @staticmethod
    def _canonical_query_string(query: str) -> str:
        if not query:
            return ""
        pairs = parse_qsl(query, keep_blank_values=True)
        encoded = sorted(
            (quote(k, safe=""), quote(v, safe="")) for k, v in pairs
        )
        return "&".join(f"{k}={v}" for k, v in encoded)

    @staticmethod
    def _canonical_headers(headers: Dict[str, str]) -> Tuple[str, str]:
        """Return (signed_headers_string, canonical_headers_block).

        Only lowercase header names are used.  Headers are sorted by name.
        ``host`` and ``x-amz-*`` headers are always included.
        """
        required_prefixes = ("host", "x-amz-", "content-type", "content-md5")
        selected = {
            k.lower(): v.strip()
            for k, v in headers.items()
            if any(k.lower().startswith(p) for p in required_prefixes)
        }
        sorted_keys = sorted(selected)
        canonical = "\n".join(f"{k}:{selected[k]}" for k in sorted_keys) + "\n"
        signed = ";".join(sorted_keys)
        return signed, canonical
