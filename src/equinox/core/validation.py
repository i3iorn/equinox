"""Input validation and sanitization module.

This module provides comprehensive validation for all user inputs
following zero-trust security principles.
"""

import ipaddress
import logging
import re
import json
import socket
import threading
import concurrent.futures
from typing import Any, Dict, Optional, Tuple
from equinox.core import urls
from urllib.parse import urlparse, unquote_plus
from pathlib import Path

from equinox.core.exceptions import ValidationError

# Module-level logger for structured logging
_logger = logging.getLogger(__name__)

# Lazy-initialised thread pool for DNS resolution in SSRF checks.
# A single-worker pool avoids creating/tearing down threads on every call.
_dns_pool: Optional[concurrent.futures.ThreadPoolExecutor] = None
_dns_pool_lock = threading.Lock()

# Canonical set of allowed HTTP methods — referenced by both
# ``Request.__post_init__`` and ``Validator.validate_method`` so there
# is a single source of truth.
VALID_HTTP_METHODS = frozenset({
    'GET', 'POST', 'PUT', 'PATCH', 'DELETE',
    'HEAD', 'OPTIONS', 'TRACE', 'CONNECT',
})


def _get_dns_pool() -> concurrent.futures.ThreadPoolExecutor:
    """Return a shared single-worker thread pool for DNS lookups.

    Thread-safe: uses double-checked locking.
    """
    global _dns_pool
    if _dns_pool is None:
        with _dns_pool_lock:
            if _dns_pool is None:
                _dns_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    return _dns_pool


class Validator:
    """Zero-trust input validator."""

    # ── Limits ────────────────────────────────────────────────────────────────

    VALID_URL_SCHEMES = {'http', 'https'}
    MAX_URL_LENGTH = 2048
    MAX_HEADER_LENGTH = 8192
    MAX_BODY_SIZE = 100 * 1024 * 1024  # 100MB
    MAX_HEADER_COUNT = 100
    MAX_PARAM_COUNT = 100
    MAX_PARAM_KEY_LENGTH = 256
    MAX_PARAM_VALUE_LENGTH = 4096
    MAX_VARIABLE_NAME_LENGTH = 128
    MAX_VARIABLE_VALUE_LENGTH = 4096

    # ── Security patterns (precompiled at class-definition time) ──────────────
    #
    # Compiling once here avoids repeated compilation on every validation call.
    # The string literals are kept as inline comments for readability.

    _SQL_INJECTION_RE: Tuple = tuple(re.compile(p, re.IGNORECASE) for p in (
        r"(\bUNION\b.*\bSELECT\b)",
        r"(\bDROP\b.*\bTABLE\b)",
        r"(\bINSERT\b.*\bINTO\b)",
        r"(\bDELETE\b.*\bFROM\b)",
        r"(\bUPDATE\b.*\bSET\b)",
        r"(--|\#|\/\*|\*\/)",
        r"(\bOR\b.*=.*)",
        r"(;.*\b(DROP|DELETE|INSERT|UPDATE)\b)",
    ))

    _COMMAND_INJECTION_RE: Tuple = tuple(re.compile(p) for p in (
        r"[;&|`$]",
        r"\$\{[^}]*\}",
        r"\$\([^)]*\)",
        r"`[^`]*`",
    ))

    # All XSS patterns — used for body/general checks.
    _XSS_RE: Tuple = tuple(re.compile(p, re.IGNORECASE) for p in (
        r"<script[^>]*>.*?</script>",
        r"javascript:",
        r"on\w+\s*=",
        r"<iframe",
        r"<object",
        r"<embed",
    ))

    # XSS patterns safe for URL/header checks.
    # The HTML-only patterns (iframe, object, embed) would produce false
    # positives on valid API endpoint paths, so they are excluded here.
    _URL_XSS_RE: Tuple = _XSS_RE[:3]

    _PATH_TRAVERSAL_RE: Tuple = tuple(re.compile(p) for p in (
        r"\.\.[/\\]",          # ../ or ..\ anywhere in path (cross-platform)
        r"(^|[/\\])\.\.$",     # trailing .. as a path component (e.g. "dir/..")
        r"^\.\.?$",            # bare "." or ".." as the entire path
        r"~/",                 # home-relative shorthand
    ))

    # Headers that httpx manages internally — setting them manually may
    # cause unexpected behaviour, but an API testing tool should allow it
    # when ``strict=False``.
    _MANAGED_HEADERS = frozenset({
        'host', 'connection', 'content-length',
        'transfer-encoding', 'upgrade',
    })

    # Cloud metadata endpoints that should always be blocked.
    _METADATA_HOSTS: frozenset = frozenset({
        "169.254.169.254",     # AWS / GCP / Azure metadata
        "metadata.google.internal",
        "metadata.goog",
    })

    # ── Private helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _require_nonempty_str(value: Any, field_name: str) -> None:
        """Raise ``ValidationError`` if *value* is not a non-empty string."""
        if not value or not isinstance(value, str):
            raise ValidationError(f"{field_name} must be a non-empty string")

    @staticmethod
    def _check_crlf(value: str, field_name: str) -> None:
        """Raise ``ValidationError`` if *value* contains CR or LF characters."""
        if '\r' in value or '\n' in value:
            raise ValidationError(
                f"{field_name} contains invalid characters (CRLF)"
            )

    @classmethod
    def _validate_json_body(cls, body: str) -> None:
        """Raise ``ValidationError`` if *body* is not valid JSON.

        Tolerates a single common deviation: trailing commas before ``}`` or
        ``]`` (e.g. ``{"a": 1,}``).  Any other structural error is reported
        using the original parse exception for a precise error message.
        """
        try:
            json.loads(body)
        except json.JSONDecodeError as original_err:
            # Strip trailing commas and retry once
            normalised = re.sub(r",(\s*[}\]])", r"\1", body)
            try:
                json.loads(normalised)
                # Normalised body is valid — trailing commas only; proceed.
            except json.JSONDecodeError:
                raise ValidationError(f"Invalid JSON body: {original_err}")

    # ── Public validators ─────────────────────────────────────────────────────

    @classmethod
    def validate_url(cls, url: str) -> str:
        """Validate a URL string with basic safety checks.

        This performs **string-level** checks only (length, XSS) and does
        **not** require a structurally complete URL.  URLs may still contain
        ``{{variable}}`` placeholders or be relative paths at this stage
        (e.g. during import).

        For full structural validation with ``urlps`` after variable
        interpolation, use :meth:`validate_resolved_url`.
        """
        cls._require_nonempty_str(url, "URL")

        url = url.strip()

        if len(url) > cls.MAX_URL_LENGTH:
            _logger.warning(
                "URL validation failed: exceeds max length",
                extra={"length": len(url), "max_length": cls.MAX_URL_LENGTH}
            )
            raise ValidationError(f"URL exceeds maximum length of {cls.MAX_URL_LENGTH}")

        for rx in cls._URL_XSS_RE:
            if rx.search(url):
                _logger.warning("URL validation failed: XSS pattern detected")
                raise ValidationError("URL contains potentially malicious content")

        _logger.debug("URL validation passed", extra={"url_length": len(url)})
        return url

    @classmethod
    def validate_resolved_url(cls, url: str) -> str:
        """Validate a fully-resolved URL using ``urlps``.

        Call this **after** all ``{{variable}}`` placeholders have been
        expanded — i.e. at send-time.  It performs full structural parsing
        (scheme, host, port, path) and rejects malformed URLs.
        """
        url = cls.validate_url(url)

        expanded = urls.expand_placeholders(url, None)
        try:
            parts = urls.normalized_parts(expanded)
        except Exception as e:
            _logger.warning(
                "URL parsing failed via urls.normalized_parts",
                extra={"error": str(e), "url_length": len(expanded)}
            )
            raise ValidationError(f"Invalid URL format: {e}")

        scheme = parts.get("scheme") or ""
        netloc = parts.get("netloc") or ""

        if scheme not in cls.VALID_URL_SCHEMES:
            _logger.warning(
                "URL validation failed: invalid scheme",
                extra={"scheme": scheme, "allowed_schemes": list(cls.VALID_URL_SCHEMES)}
            )
            raise ValidationError(
                f"Invalid URL scheme: {scheme}. "
                f"Allowed schemes: {', '.join(cls.VALID_URL_SCHEMES)}"
            )

        if not netloc:
            _logger.warning("URL validation failed: missing hostname")
            raise ValidationError("URL must contain a hostname")

        parsed_host = urlparse(expanded).hostname
        if parsed_host:
            cls._check_ssrf(parsed_host)

        _logger.debug(
            "URL validation passed",
            extra={"scheme": scheme, "host": parsed_host or "unknown"}
        )
        return url

    @classmethod
    def _check_ssrf(cls, hostname: str) -> None:
        """Block requests to private/internal IPs and cloud metadata endpoints.

        This is a best-effort check at URL construction time. It does NOT
        prevent DNS rebinding attacks (that requires runtime enforcement).

        Raises:
            ValidationError: If the hostname resolves to a private IP or is
                a known metadata endpoint.
        """
        hostname_lower = hostname.lower().rstrip(".")

        if hostname_lower in cls._METADATA_HOSTS:
            _logger.warning(
                "SSRF protection: blocked metadata endpoint request",
                extra={"hostname": hostname}
            )
            raise ValidationError(
                f"Requests to metadata endpoint '{hostname}' are blocked (SSRF protection)"
            )

        try:
            addr = ipaddress.ip_address(hostname_lower)
            if addr.is_private or addr.is_loopback or addr.is_link_local:
                raise ValidationError(
                    f"Requests to private/internal IP '{hostname}' are blocked (SSRF protection)"
                )
        except ValueError:
            # Not a literal IP — try DNS resolution with a tight timeout.
            future = None
            try:
                pool = _get_dns_pool()
                future = pool.submit(
                    socket.getaddrinfo,
                    hostname_lower, None, socket.AF_UNSPEC, socket.SOCK_STREAM,
                )
                infos = future.result(timeout=2.0)
                for family, _, _, _, sockaddr in infos:
                    ip_str = sockaddr[0]
                    addr = ipaddress.ip_address(ip_str)
                    if addr.is_private or addr.is_loopback or addr.is_link_local:
                        raise ValidationError(
                            f"Hostname '{hostname}' resolves to private IP {ip_str} "
                            f"(SSRF protection)"
                        )
            except (socket.gaierror, OSError):
                pass  # DNS resolution failed — allow (will fail at connect time)
            except concurrent.futures.TimeoutError:
                if future is not None:
                    future.cancel()
                # DNS timed out — allow (will fail at connect time)

    @classmethod
    def validate_header_name(cls, name: str, *, strict: bool = True) -> str:
        """Validate HTTP header name.

        Args:
            name: Header name to validate
            strict: When *True* (default) managed headers like ``Host``
                and ``Content-Length`` are rejected.  Pass ``strict=False``
                from the send path to allow them with a logged warning —
                useful for an API testing tool where users may want to
                override transport-level headers intentionally.

        Returns:
            Validated header name

        Raises:
            ValidationError: If header name is invalid
        """
        cls._require_nonempty_str(name, "Header name")

        name = name.strip()

        if len(name) > 256:
            raise ValidationError("Header name too long")

        if not re.match(r'^[a-zA-Z0-9!#$%&\'*+\-.^_`|~]+$', name):
            raise ValidationError(f"Invalid header name format: {name}")

        if name.lower() in cls._MANAGED_HEADERS:
            if strict:
                raise ValidationError(f"Cannot manually set header: {name}")
            else:
                _logger.warning(
                    "Header '%s' is normally managed by the HTTP transport layer "
                    "— overriding it may cause unexpected behaviour.", name,
                )

        return name

    @classmethod
    def validate_header_value(cls, value: str) -> str:
        """Validate HTTP header value.

        Args:
            value: Header value to validate

        Returns:
            Validated header value

        Raises:
            ValidationError: If header value is invalid
        """
        if not isinstance(value, str):
            raise ValidationError("Header value must be a string")

        if len(value) > cls.MAX_HEADER_LENGTH:
            raise ValidationError("Header value too long")

        cls._check_crlf(value, "Header value")

        for rx in cls._URL_XSS_RE:
            if rx.search(value):
                raise ValidationError("Header value contains potentially malicious content")

        return value

    @classmethod
    def validate_headers(cls, headers: Dict[str, str], *, strict: bool = True) -> Dict[str, str]:
        """Validate all headers.

        Args:
            headers: Dictionary of headers
            strict: Passed through to :meth:`validate_header_name`.

        Returns:
            Validated headers

        Raises:
            ValidationError: If headers are invalid
        """
        if not isinstance(headers, dict):
            raise ValidationError("Headers must be a dictionary")

        if len(headers) > cls.MAX_HEADER_COUNT:
            raise ValidationError(f"Too many headers (max: {cls.MAX_HEADER_COUNT})")

        validated = {}
        for name, value in headers.items():
            validated_name = cls.validate_header_name(name, strict=strict)
            validated_value = cls.validate_header_value(str(value))
            validated[validated_name] = validated_value

        return validated

    @classmethod
    def validate_request_body(cls, body: Any, content_type: Optional[str] = None) -> Any:
        """Validate request body.

        Args:
            body: Request body
            content_type: Content-Type header value

        Returns:
            Validated body

        Raises:
            ValidationError: If body is invalid
        """
        if body is None:
            return None

        if isinstance(body, dict):
            try:
                body_str = json.dumps(body)
            except (TypeError, ValueError) as e:
                raise ValidationError(f"Invalid JSON body: {e}")
        else:
            body_str = str(body)

        body_size = len(body_str.encode('utf-8'))
        if body_size > cls.MAX_BODY_SIZE:
            raise ValidationError(
                f"Request body too large: {body_size} bytes "
                f"(max: {cls.MAX_BODY_SIZE} bytes)"
            )

        if content_type and 'json' in content_type.lower() and isinstance(body, str):
            cls._validate_json_body(body)

        if isinstance(body, str):
            for rx in cls._SQL_INJECTION_RE:
                if rx.search(body):
                    _logger.warning(
                        "Potential SQL injection pattern detected in request body"
                    )
                    break  # One warning per body is enough

        return body

    @classmethod
    def validate_query_params(cls, params: Dict[str, Any]) -> Dict[str, Any]:
        """Validate query parameters.

        Args:
            params: Dictionary of query parameters

        Returns:
            Validated parameters

        Raises:
            ValidationError: If parameters are invalid
        """
        if not isinstance(params, dict):
            raise ValidationError("Query parameters must be a dictionary")

        if len(params) > cls.MAX_PARAM_COUNT:
            raise ValidationError(f"Too many parameters (max: {cls.MAX_PARAM_COUNT})")

        validated = {}
        for key, value in params.items():
            if not isinstance(key, str):
                raise ValidationError("Parameter key must be a string")

            if len(key) > cls.MAX_PARAM_KEY_LENGTH:
                raise ValidationError("Parameter key too long")

            cls._check_crlf(key, f"Parameter key '{key}'")

            value_str = str(value)
            if len(value_str) > cls.MAX_PARAM_VALUE_LENGTH:
                raise ValidationError("Parameter value too long")

            cls._check_crlf(value_str, "Parameter value")

            validated[key] = value

        return validated

    @classmethod
    def validate_file_path(cls, path: str, base_dir: Optional[Path] = None) -> Path:
        """Validate file path to prevent directory traversal.

        Args:
            path: File path to validate
            base_dir: Base directory to restrict access to

        Returns:
            Validated Path object

        Raises:
            ValidationError: If path is invalid or attempts traversal
        """
        cls._require_nonempty_str(path, "Path")

        # Check both raw path and URL-decoded form for traversal patterns
        # (e.g. "..%2F" is URL-encoded "../")
        for candidate in (path, unquote_plus(path)):
            for rx in cls._PATH_TRAVERSAL_RE:
                if rx.search(candidate):
                    raise ValidationError(f"Path contains traversal pattern: {path}")

        try:
            file_path = Path(path).resolve()
        except Exception as e:
            raise ValidationError(f"Invalid path: {e}")

        if base_dir:
            base_dir = base_dir.resolve()
            try:
                file_path.relative_to(base_dir)
            except ValueError:
                raise ValidationError(f"Path outside allowed directory: {path}")

        return file_path

    @classmethod
    def validate_environment_variable(cls, name: str, value: str) -> Tuple[str, str]:
        """Validate environment variable.

        Args:
            name: Variable name
            value: Variable value

        Returns:
            Tuple of (validated_name, validated_value)

        Raises:
            ValidationError: If variable is invalid
        """
        cls._require_nonempty_str(name, "Variable name")

        if not isinstance(value, str):
            raise ValidationError("Variable value must be a string")

        if not re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', name):
            raise ValidationError(
                f"Invalid variable name: {name}. "
                "Must start with a letter or underscore and contain only letters, digits, and underscores"
            )

        if len(name) > cls.MAX_VARIABLE_NAME_LENGTH:
            raise ValidationError("Variable name too long")

        if len(value) > cls.MAX_VARIABLE_VALUE_LENGTH:
            raise ValidationError("Variable value too long")

        for rx in cls._COMMAND_INJECTION_RE:
            if rx.search(value):
                raise ValidationError("Variable value contains potentially dangerous pattern")

        return name, value

    @classmethod
    def sanitize_for_display(cls, text: str, max_length: int = 1000) -> str:
        """Sanitize text for safe display.

        Args:
            text: Text to sanitize
            max_length: Maximum length to return

        Returns:
            Sanitized text
        """
        if not isinstance(text, str):
            text = str(text)

        if len(text) > max_length:
            text = text[:max_length] + "..."

        # Remove control characters except newlines and tabs
        text = re.sub(r'[\x00-\x08\x0B-\x0C\x0E-\x1F\x7F]', '', text)

        return text

    @classmethod
    def validate_method(cls, method: str) -> str:
        """Validate HTTP method.

        Args:
            method: HTTP method

        Returns:
            Validated method (uppercase)

        Raises:
            ValidationError: If method is invalid
        """
        cls._require_nonempty_str(method, "HTTP method")

        method = method.upper().strip()

        if method not in VALID_HTTP_METHODS:
            raise ValidationError(f"Invalid HTTP method: {method}")

        return method
