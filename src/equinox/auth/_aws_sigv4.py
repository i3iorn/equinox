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
import logging
from datetime import datetime, timezone
from typing import Any

from collections.abc import Callable
from urllib.parse import quote

from equinox.auth._base import AuthError, AuthStrategy, _interpolate_field, _validate_credential
from equinox.core.urls import parse_query_pairs, url_metadata

logger = logging.getLogger(__name__)


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
    DISPLAY_NAME = "AWS SigV4"

    def __init__(
        self,
        access_key: str,
        secret_key: str,
        region: str,
        service: str,
        session_token: str | None = None,
    ) -> None:
        # Validate all fields for type, length, and CRLF injection.
        # access_key / secret_key / session_token are injected into HTTP
        # headers — CRLF would allow header-injection attacks.
        self.access_key = _validate_credential(access_key, "AWS access_key")
        self.secret_key = _validate_credential(secret_key, "AWS secret_key")
        self.region = _validate_credential(region, "AWS region")
        self.service = _validate_credential(service, "AWS service")
        self.session_token: str | None = (
            _validate_credential(session_token, "AWS session_token") if session_token else None
        )

    # ── AuthStrategy interface ────────────────────────────────────────────────

    def apply(self, request: Any, headers: dict[str, str]) -> None:
        """Inject AWS SigV4 authentication headers into the request.

        Args:
            request: Request-like object with `url`, `method`, and optional `body`.
            headers: Mutable mapping of HTTP headers to update.

        Raises:
            AuthError: If required request fields are missing or invalid.
        """
        url = self._extract_url(request)
        method = self._extract_method(request)
        try:
            body_bytes = self._extract_body_bytes(request)
        except AuthError:
            logger.debug("Failed to extract request body, defaulting to empty", exc_info=True)
            body_bytes = b""

        timestamp = datetime.now(timezone.utc)
        amz_date, date_stamp = self._format_timestamps(timestamp)

        self._set_basic_headers(headers, amz_date)
        canonical_uri, canonical_qs, host_header = self._canonicalize_url_parts(url)
        headers["host"] = host_header

        signed_headers, canonical_headers = self._canonical_headers(headers)
        payload_hash = hashlib.sha256(body_bytes).hexdigest()
        headers["x-amz-content-sha256"] = payload_hash

        canonical_request = self._build_canonical_request(
            method=method,
            canonical_uri=canonical_uri,
            canonical_qs=canonical_qs,
            canonical_headers=canonical_headers,
            signed_headers=signed_headers,
            payload_hash=payload_hash,
        )

        credential_scope = f"{date_stamp}/{self.region}/{self.service}/aws4_request"
        string_to_sign = self._build_string_to_sign(
            amz_date=amz_date,
            credential_scope=credential_scope,
            canonical_request=canonical_request,
        )

        signing_key = self._get_signing_key(date_stamp)
        signature = hmac.new(
            signing_key,
            string_to_sign.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        headers["Authorization"] = (
            f"AWS4-HMAC-SHA256 Credential={self.access_key}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )

    def _extract_url(self, request: Any) -> str:
        """Return validated request URL."""
        url = getattr(request, "url", None)
        if not isinstance(url, str) or not url:
            raise AuthError("Request URL is missing or invalid")
        return url

    def _extract_method(self, request: Any) -> str:
        """Return validated uppercase HTTP method."""
        method = getattr(request, "method", "GET")
        if not isinstance(method, str) or not method.strip():
            raise AuthError("Request method is missing or invalid")
        return method.upper()

    def _extract_body_bytes(self, request: Any) -> bytes:
        """Return request body as bytes, defaulting to empty bytes."""
        body = getattr(request, "body", b"")
        if isinstance(body, bytes):
            return body
        if isinstance(body, str):
            return body.encode("utf-8")
        raise AuthError("Request body must be str or bytes")

    def _format_timestamps(self, ts: datetime) -> tuple[str, str]:
        """Return (amz_date, date_stamp) strings."""
        return ts.strftime("%Y%m%dT%H%M%SZ"), ts.strftime("%Y%m%d")

    def _set_basic_headers(self, headers: dict[str, str], amz_date: str) -> None:
        """Set x-amz-date and optional session token."""
        headers["x-amz-date"] = amz_date
        if self.session_token:
            headers["x-amz-security-token"] = self.session_token

    def _canonicalize_url_parts(self, url: str) -> tuple[str, str, str]:
        """Return canonical_uri, canonical_query_string, host_header."""
        parsed = url_metadata(url)

        path = str(parsed.get("path") or "")
        query = str(parsed.get("query") or "")
        host = str(parsed.get("hostname") or "")

        port = parsed.get("port")
        if isinstance(port, int) and port not in (80, 443):
            host = f"{host}:{port}"

        canonical_uri = self._canonical_uri(path)
        canonical_qs = self._canonical_query_string(query)
        return canonical_uri, canonical_qs, host

    def _build_canonical_request(
        self,
        method: str,
        canonical_uri: str,
        canonical_qs: str,
        canonical_headers: str,
        signed_headers: str,
        payload_hash: str,
    ) -> str:
        """Return canonical request string."""
        return "\n".join(
            [
                method,
                canonical_uri,
                canonical_qs,
                canonical_headers,
                signed_headers,
                payload_hash,
            ],
        )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "type": self.AUTH_TYPE,
            "access_key": self.access_key,
            "secret_key": self.secret_key,
            "region": self.region,
            "service": self.service,
        }
        if self.session_token:
            d["session_token"] = self.session_token
        return d

    def _build_string_to_sign(
        self,
        amz_date: str,
        credential_scope: str,
        canonical_request: str,
    ) -> str:
        """Return SigV4 string-to-sign."""
        hashed_request = hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()
        return "\n".join(
            [
                "AWS4-HMAC-SHA256",
                amz_date,
                credential_scope,
                hashed_request,
            ],
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any], **kwargs: Any) -> AWSSigV4Auth:
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

    # ── Strategy metadata ─────────────────────────────────────────────────────

    def interpolate(self, interp: Callable[[str], str]) -> AWSSigV4Auth:
        """Return a copy with ``{{VAR}}`` placeholders expanded."""
        return AWSSigV4Auth(
            access_key=interp(self.access_key),
            secret_key=interp(self.secret_key),
            region=interp(self.region),
            service=interp(self.service),
            session_token=_interpolate_field(self.session_token, interp),
        )

    def get_display_summary(self) -> str:
        masked_key = f"{self.access_key[:4]}****" if len(self.access_key) > 4 else "****"
        return f"Key: {masked_key}  Region: {self.region}  Service: {self.service}"

    def get_preflight_warning(self) -> str | None:
        if not self.access_key:
            return "AWS access key is empty"
        if not self.secret_key:
            return "AWS secret key is empty"
        return None

    def __repr__(self) -> str:
        masked_key = f"{self.access_key[:4]}****" if len(self.access_key) > 4 else "****"
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
        pairs = parse_query_pairs(query, keep_blank_values=True)
        encoded = sorted((quote(k, safe=""), quote(v, safe="")) for k, v in pairs)
        return "&".join(f"{k}={v}" for k, v in encoded)

    @staticmethod
    def _canonical_headers(headers: dict[str, str]) -> tuple[str, str]:
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
