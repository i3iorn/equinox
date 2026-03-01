"""Input validation and sanitization module.

This module provides comprehensive validation for all user inputs
following zero-trust security principles.
"""

import ipaddress
import re
import json
import socket
from typing import Any, Dict, Optional
from urlps import parse_url_unsafe as _urlps_parse
from pathlib import Path

from equinox.core.exceptions import ValidationError


class Validator:
    """Zero-trust input validator."""

    # URL validation patterns
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

    # Dangerous patterns
    SQL_INJECTION_PATTERNS = [
        r"(\bUNION\b.*\bSELECT\b)",
        r"(\bDROP\b.*\bTABLE\b)",
        r"(\bINSERT\b.*\bINTO\b)",
        r"(\bDELETE\b.*\bFROM\b)",
        r"(\bUPDATE\b.*\bSET\b)",
        r"(--|\#|\/\*|\*\/)",
        r"(\bOR\b.*=.*)",
        r"(;.*\b(DROP|DELETE|INSERT|UPDATE)\b)",
    ]

    COMMAND_INJECTION_PATTERNS = [
        r"[;&|`$]",
        r"\$\{[^}]*\}",
        r"\$\([^)]*\)",
        r"`[^`]*`",
    ]

    XSS_PATTERNS = [
        r"<script[^>]*>.*?</script>",
        r"javascript:",
        r"on\w+\s*=",
        r"<iframe",
        r"<object",
        r"<embed",
    ]

    PATH_TRAVERSAL_PATTERNS = [
        r"\.\.[/\\]",          # ../ or ..\ anywhere in path (cross-platform)
        r"(^|[/\\])\.\.$",     # trailing .. as a path component (e.g. "dir/..")
        r"^\.\.?$",            # bare "." or ".." as the entire path
        r"~/",                 # home-relative shorthand
    ]

    # Only the first 3 XSS patterns are relevant to URL/header checks
    # (script tags, javascript: scheme, inline event handlers).
    # The remaining patterns (iframe, object, embed) are HTML-only and
    # would cause false positives on valid API URLs/query params.
    _URL_SAFE_XSS_PATTERN_COUNT = 3

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
        if not url or not isinstance(url, str):
            raise ValidationError("URL must be a non-empty string")

        url = url.strip()

        if len(url) > cls.MAX_URL_LENGTH:
            raise ValidationError(f"URL exceeds maximum length of {cls.MAX_URL_LENGTH}")

        # XSS / injection checks on the raw string
        for pattern in cls.XSS_PATTERNS[:cls._URL_SAFE_XSS_PATTERN_COUNT]:
            if re.search(pattern, url, re.IGNORECASE):
                raise ValidationError("URL contains potentially malicious content")

        return url

    @classmethod
    def validate_resolved_url(cls, url: str) -> str:
        """Validate a fully-resolved URL using ``urlps``.

        Call this **after** all ``{{variable}}`` placeholders have been
        expanded — i.e. at send-time.  It performs full structural parsing
        (scheme, host, port, path) and rejects malformed URLs.
        """
        # Run the basic string checks first
        url = cls.validate_url(url)

        try:
            parsed = _urlps_parse(url)
        except Exception as e:
            err_msg = str(e).lower()
            if "host" in err_msg:
                raise ValidationError("URL must contain a hostname")
            raise ValidationError(f"Invalid URL format: {e}")

        if parsed.scheme not in cls.VALID_URL_SCHEMES:
            raise ValidationError(
                f"Invalid URL scheme: {parsed.scheme}. "
                f"Allowed schemes: {', '.join(cls.VALID_URL_SCHEMES)}"
            )

        if not parsed.netloc:
            raise ValidationError("URL must contain a hostname")

        hostname = parsed.host
        if hostname:
            cls._check_ssrf(hostname)

        return url

    # Cloud metadata endpoints that should always be blocked
    _METADATA_HOSTS: frozenset = frozenset({
        "169.254.169.254",     # AWS / GCP / Azure metadata
        "metadata.google.internal",
        "metadata.goog",
    })

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

        # Block known metadata endpoints
        if hostname_lower in cls._METADATA_HOSTS:
            raise ValidationError(
                f"Requests to metadata endpoint '{hostname}' are blocked (SSRF protection)"
            )

        # Try to resolve and check if the IP is private
        try:
            addr = ipaddress.ip_address(hostname_lower)
            if addr.is_private or addr.is_loopback or addr.is_link_local:
                raise ValidationError(
                    f"Requests to private/internal IP '{hostname}' are blocked (SSRF protection)"
                )
        except ValueError:
            # Not a literal IP — try DNS resolution
            try:
                infos = socket.getaddrinfo(hostname_lower, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
                for family, _, _, _, sockaddr in infos:
                    ip_str = sockaddr[0]
                    addr = ipaddress.ip_address(ip_str)
                    if addr.is_private or addr.is_loopback or addr.is_link_local:
                        raise ValidationError(
                            f"Hostname '{hostname}' resolves to private IP {ip_str} "
                            f"(SSRF protection)"
                        )
            except socket.gaierror:
                pass  # DNS resolution failed — allow (will fail at connect time)

    @classmethod
    def validate_header_name(cls, name: str) -> str:
        """Validate HTTP header name.

        Args:
            name: Header name to validate

        Returns:
            Validated header name

        Raises:
            ValidationError: If header name is invalid
        """
        if not name or not isinstance(name, str):
            raise ValidationError("Header name must be a non-empty string")

        name = name.strip()

        # Check length
        if len(name) > 256:
            raise ValidationError("Header name too long")

        # Validate format (RFC 7230)
        if not re.match(r'^[a-zA-Z0-9!#$%&\'*+\-.^_`|~]+$', name):
            raise ValidationError(f"Invalid header name format: {name}")

        # Prevent dangerous headers
        dangerous_headers = {
            'host', 'connection', 'content-length',
            'transfer-encoding', 'upgrade'
        }
        if name.lower() in dangerous_headers:
            raise ValidationError(f"Cannot manually set header: {name}")

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

        # Check length
        if len(value) > cls.MAX_HEADER_LENGTH:
            raise ValidationError("Header value too long")

        # Check for CRLF injection
        if '\r' in value or '\n' in value:
            raise ValidationError("Header value contains invalid characters (CRLF)")

        # Check for XSS patterns in headers
        for pattern in cls.XSS_PATTERNS[:cls._URL_SAFE_XSS_PATTERN_COUNT]:
            if re.search(pattern, value, re.IGNORECASE):
                raise ValidationError("Header value contains potentially malicious content")

        return value

    @classmethod
    def validate_headers(cls, headers: Dict[str, str]) -> Dict[str, str]:
        """Validate all headers.

        Args:
            headers: Dictionary of headers

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
            validated_name = cls.validate_header_name(name)
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

        # Convert to string for size check
        if isinstance(body, dict):
            try:
                body_str = json.dumps(body)
            except (TypeError, ValueError) as e:
                raise ValidationError(f"Invalid JSON body: {e}")
        else:
            body_str = str(body)

        # Check size
        body_size = len(body_str.encode('utf-8'))
        if body_size > cls.MAX_BODY_SIZE:
            raise ValidationError(
                f"Request body too large: {body_size} bytes "
                f"(max: {cls.MAX_BODY_SIZE} bytes)"
            )

        # Validate JSON if content type indicates JSON
        if content_type and 'json' in content_type.lower():
            if isinstance(body, str):
                try:
                    json.loads(body)
                except json.JSONDecodeError as e:
                    raise ValidationError(f"Invalid JSON body: {e}")

        # Check for SQL injection patterns in string bodies.
        # These are heuristic detections only — we do not block because the
        # body might legitimately contain SQL (e.g., a query editor app).
        # Log a warning so security teams can audit suspicious traffic.
        if isinstance(body, str):
            import logging as _logging
            _sql_logger = _logging.getLogger(__name__)
            for pattern in cls.SQL_INJECTION_PATTERNS:
                if re.search(pattern, body, re.IGNORECASE):
                    _sql_logger.warning(
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
            # Validate key
            if not isinstance(key, str):
                raise ValidationError("Parameter key must be a string")

            if len(key) > cls.MAX_PARAM_KEY_LENGTH:
                raise ValidationError("Parameter key too long")

            # Check for command injection in keys
            for pattern in cls.COMMAND_INJECTION_PATTERNS:
                if re.search(pattern, key):
                    raise ValidationError(f"Parameter key contains invalid characters: {key}")

            # Validate value
            value_str = str(value)
            if len(value_str) > cls.MAX_PARAM_VALUE_LENGTH:
                raise ValidationError("Parameter value too long")

            # Check for command injection in values
            for pattern in cls.COMMAND_INJECTION_PATTERNS:
                if re.search(pattern, value_str):
                    raise ValidationError(f"Parameter value contains invalid characters")

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
        if not path or not isinstance(path, str):
            raise ValidationError("Path must be a non-empty string")

        # Check both raw path and URL-decoded form for traversal patterns
        # (e.g. "..%2F" is URL-encoded "../")
        from urlps._parser import unquote_plus as _unquote
        paths_to_check = [path, _unquote(path)]
        for candidate in paths_to_check:
            for pattern in cls.PATH_TRAVERSAL_PATTERNS:
                if re.search(pattern, candidate):
                    raise ValidationError(f"Path contains traversal pattern: {path}")

        try:
            # Convert to Path and resolve
            file_path = Path(path).resolve()
        except Exception as e:
            raise ValidationError(f"Invalid path: {e}")

        # If base_dir specified, ensure path is within it
        if base_dir:
            base_dir = base_dir.resolve()
            try:
                file_path.relative_to(base_dir)
            except ValueError:
                raise ValidationError(f"Path outside allowed directory: {path}")

        return file_path

    @classmethod
    def validate_environment_variable(cls, name: str, value: str) -> tuple[str, str]:
        """Validate environment variable.

        Args:
            name: Variable name
            value: Variable value

        Returns:
            Tuple of (validated_name, validated_value)

        Raises:
            ValidationError: If variable is invalid
        """
        if not name or not isinstance(name, str):
            raise ValidationError("Variable name must be a non-empty string")

        if not isinstance(value, str):
            raise ValidationError("Variable value must be a string")

        # Validate name format (POSIX: letters, digits, underscore; must not start with digit)
        if not re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', name):
            raise ValidationError(
                f"Invalid variable name: {name}. "
                "Must start with a letter or underscore and contain only letters, digits, and underscores"
            )

        # Check length
        if len(name) > cls.MAX_VARIABLE_NAME_LENGTH:
            raise ValidationError("Variable name too long")

        if len(value) > cls.MAX_VARIABLE_VALUE_LENGTH:
            raise ValidationError("Variable value too long")

        # Check for code injection patterns
        for pattern in cls.COMMAND_INJECTION_PATTERNS:
            if re.search(pattern, value):
                raise ValidationError(f"Variable value contains potentially dangerous pattern")

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

        # Truncate if too long
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
        if not method or not isinstance(method, str):
            raise ValidationError("HTTP method must be a non-empty string")

        method = method.upper().strip()

        valid_methods = {
            'GET', 'POST', 'PUT', 'PATCH', 'DELETE',
            'HEAD', 'OPTIONS', 'TRACE', 'CONNECT'
        }

        if method not in valid_methods:
            raise ValidationError(f"Invalid HTTP method: {method}")

        return method
