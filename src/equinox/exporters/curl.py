"""cURL exporter — converts a single request to a ``curl`` command string."""
from __future__ import annotations

import logging
import platform
import shlex

from equinox.core.security import redact_headers
from equinox.core.validation import Validator
from equinox.core import urls
from equinox.core.request import Request

__all__ = ["CurlExporter"]

logger = logging.getLogger(__name__)


class CurlExporter:
    """Export individual requests as cURL commands.

    All sensitive header values are redacted before being embedded in the
    command string so the output is safe to log or share.
    """

    @staticmethod
    def _shell_quote(s: str) -> str:
        """Shell-escape *s* with platform-appropriate quoting.

        Uses :func:`shlex.quote` on Unix/macOS and double-quote wrapping on
        Windows, where ``shlex.quote`` produces POSIX-style single quotes
        that ``cmd.exe`` does not handle.

        Args:
            s: String to escape.

        Returns:
            Shell-safe quoted string.
        """
        if platform.system() == "Windows":
            return '"' + s.replace('"', '\\"') + '"'
        return shlex.quote(s)

    @staticmethod
    def export_request(request: Request) -> str:
        """Return a ``curl`` command string equivalent to *request*.

        Args:
            request: The request to export.

        Returns:
            A ``curl …`` shell command string.

        Raises:
            ValidationError: If *request.url* fails URL validation.
        """
        Validator.validate_url(request.url)

        quote  = CurlExporter._shell_quote
        parts  = ["curl", "-X", request.method]

        for key, value in redact_headers(request.headers or {}).items():
            parts.append(f"-H {quote(f'{key}: {value}')}")

        if request.body:
            parts.append(f"-d {quote(request.body)}")

        base_url = urls.expand_placeholders(
            request.url,
            getattr(request, "path_params", None) or None,
        )
        if request.params:
            sep = "&" if "?" in base_url else "?"
            qs  = "&".join(f"{k}={v}" for k, v in request.params.items())
            parts.append(quote(f"{base_url}{sep}{qs}"))
        else:
            parts.append(quote(base_url))

        return " ".join(parts)


