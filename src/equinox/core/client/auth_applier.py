"""Auth-application step for the HTTP client pipeline.

Resolves the active :class:`~equinox.auth.base.AuthStrategy` for a request,
calls its ``apply()`` method, and returns only the headers that were added so
the dispatcher can attach them via httpx's redirect-safe auth flow.
"""
import logging
from typing import Dict, Optional

from equinox.auth import AuthStrategy
from equinox.core.exceptions import RequestError
from equinox.core.request import Request
from equinox.security import redact_url, redact_body

logger = logging.getLogger(__name__)

__all__ = ["AuthApplier"]

# Substrings (lowercased) that indicate the proxy refused the TCP connection.
# Checked against the redacted error message to emit an actionable hint.
_PROXY_REFUSED_MARKERS: frozenset[str] = frozenset({
    "10061", "connection refused", "econnrefused"
})


def _is_proxy_connection_refused(message: str) -> bool:
    """Return ``True`` when *message* suggests the proxy refused a TCP connection.

    Args:
        message: Error message (lowercased before checking).
    """
    lower_msg = message.lower()
    return any(marker in lower_msg for marker in _PROXY_REFUSED_MARKERS)


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
        if not request:
            raise ValueError("request cannot be None")

        auth_strategy = explicit_auth or request.auth
        if not auth_strategy:
            safe_url = redact_url(request.url) if request.url else ""
            logger.debug("No auth strategy active for %s %s", request.method, safe_url)
            return {}

        pre_keys = set(headers.keys())
        self._invoke_strategy(auth_strategy, request, headers, proxy)

        # Isolate only the headers the strategy injected.
        added_keys = set(headers.keys()) - pre_keys
        auth_headers = {k: headers[k] for k in added_keys}

        if auth_headers:
            logger.debug(
                "Auth applied (%s): added headers %s",
                type(auth_strategy).__name__,
                sorted(auth_headers.keys()),
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
        """Call ``strategy.apply()``, converting any exception to :class:`RequestError`.

        Args:
            strategy: The auth strategy to apply.
            request: The request object (passed to strategy).
            headers: Mutable headers dict (modified by strategy).
            proxy: Optional proxy URL to inject into strategy if supported.

        Raises:
            RequestError: If strategy.apply() raises any exception.
        """
        verify_ssl = bool(getattr(request, "verify_ssl", True))
        logger.debug("Applying auth strategy: %s", type(strategy).__name__)
        try:
            # Prefer explicit runtime context over mutating strategy internals.
            apply_with_context = getattr(strategy, "apply_with_context", None)
            if callable(apply_with_context):
                apply_with_context(
                    request,
                    headers,
                    proxy=proxy,
                    verify_ssl=verify_ssl,
                )
            else:
                strategy.apply(request, headers)
        except Exception as exc:
            raise self._map_auth_error(exc, strategy, proxy) from exc

    @staticmethod
    def _map_auth_error(
        exc: Exception,
        strategy: AuthStrategy,
        proxy: Optional[str],
    ) -> RequestError:
        """Build a descriptive :class:`RequestError` from a raw auth exception.

        Args:
            exc: The exception raised by the strategy.
            strategy: The auth strategy that failed.
            proxy: Optional proxy in use (for context in error).

        Returns:
            A RequestError with user-friendly message and context.
        """
        safe_msg = redact_body(str(exc), max_length=200) or "unknown error"
        logger.error(
            "Authentication failed (%s via %s): %s",
            type(strategy).__name__,
            type(exc).__name__,
            safe_msg,
        )
        if proxy and _is_proxy_connection_refused(safe_msg):
            return RequestError(
                f"Authentication failed — proxy ({proxy}) is not reachable. "
                "Please check your proxy settings under Preferences.",
                details={"proxy": proxy, "strategy": type(strategy).__name__},
            )
        return RequestError(f"Authentication failed: {safe_msg}")
