"""Auth-application step for the HTTP client pipeline.

Resolves the active :class:`~equinox.auth.base.AuthStrategy` for a request,
calls its ``apply()`` method, and returns only the headers that were added so
the dispatcher can attach them via httpx's redirect-safe auth flow.
"""
import logging
from typing import Dict, Optional

from equinox.auth.base import AuthStrategy
from equinox.core.exceptions import RequestError
from equinox.core.redact import redact_body
from equinox.core.request import Request

logger = logging.getLogger(__name__)

# Substrings (lowercased) that indicate the proxy refused the TCP connection.
# Checked against the redacted error message to emit an actionable hint.
_PROXY_REFUSED_MARKERS = ("10061", "connection refused", "econnrefused")


def _is_proxy_connection_refused(message: str) -> bool:
    """Return ``True`` when *message* suggests the proxy refused a TCP connection."""
    lower = message.lower()
    return any(marker in lower for marker in _PROXY_REFUSED_MARKERS)


class AuthApplier:
    """Applies an :class:`~equinox.auth.base.AuthStrategy` to outgoing request headers.

    Returns only the headers *added* by the strategy so the dispatcher can
    route them through httpx's redirect-safe auth flow rather than embedding
    them as plain headers (which httpx strips on cross-origin redirects).
    """

    def apply(
        self,
        request: Request,
        headers: Dict[str, str],
        explicit_auth: Optional[AuthStrategy],
        proxy: Optional[str],
    ) -> Dict[str, str]:
        """Apply *explicit_auth* (or ``request.auth``) to *headers*.

        Args:
            request:       The outgoing request; consulted for ``request.auth``
                           when *explicit_auth* is ``None``.
            headers:       Mutable header dict populated in-place by the strategy.
            explicit_auth: Auth strategy supplied at call-site; takes precedence
                           over ``request.auth`` when provided.
            proxy:         Active proxy URL forwarded to strategies that need it
                           (e.g. OAuth2 token refresh).

        Returns:
            A dict containing only the headers added by the strategy
            (empty when no strategy is active).

        Raises:
            RequestError: If the auth strategy raises, with a user-friendly
                          message.  Proxy connection failures receive an
                          actionable hint about checking proxy settings.
        """
        auth_strategy = explicit_auth or request.auth
        if not auth_strategy:
            return {}

        pre_auth_keys = set(headers.keys())
        self._invoke_strategy(auth_strategy, request, headers, proxy)
        auth_headers = {k: headers[k] for k in headers if k not in pre_auth_keys}

        if auth_headers:
            logger.debug(
                "Auth applied (%s): %s",
                type(auth_strategy).__name__,
                ", ".join(auth_headers.keys()),
            )
        return auth_headers

    # ── Private helpers ───────────────────────────────────────────────────────

    def _invoke_strategy(
        self,
        strategy: AuthStrategy,
        request: Request,
        headers: Dict[str, str],
        proxy: Optional[str],
    ) -> None:
        """Call ``strategy.apply()``, converting any exception to :class:`RequestError`."""
        try:
            if proxy and hasattr(strategy, "_proxy"):
                strategy._proxy = proxy
            logger.debug("Applying auth strategy: %s", type(strategy).__name__)
            strategy.apply(request, headers)
        except Exception as exc:
            raise self._map_auth_error(exc, strategy, proxy) from exc

    @staticmethod
    def _map_auth_error(
        exc: Exception,
        strategy: AuthStrategy,
        proxy: Optional[str],
    ) -> RequestError:
        """Build a descriptive :class:`RequestError` from a raw auth exception."""
        safe_msg = redact_body(str(exc), max_length=200) or "unknown error"
        logger.error(
            "Authentication failed (%s): %s — %s",
            type(exc).__name__,
            type(strategy).__name__,
            safe_msg,
        )
        if proxy and _is_proxy_connection_refused(safe_msg):
            return RequestError(
                f"OAuth2 token refresh failed — proxy ({proxy}) is not reachable. "
                "Please check your proxy settings under Preferences.",
                details={"proxy": proxy},
            )
        return RequestError(f"Authentication failed: {safe_msg}")
