from typing import Dict, Optional

from equinox import Request, AuthStrategy
from equinox.core import RequestError
from equinox.core.client import logger
from equinox.core.redact import redact_body


class AuthApplier:
    def apply(
        self,
        request: Request,
        headers: Dict[str, str],
        explicit_auth: Optional[AuthStrategy],
        proxy: Optional[str],
    ) -> Dict[str, str]:
        """Apply auth strategy and return the headers that were added.

        Raises:
            RequestError: If authentication fails.
        """
        auth_strategy = explicit_auth or request.auth
        if not auth_strategy:
            return {}

        snapshot = set(headers.keys())
        try:
            if proxy and hasattr(auth_strategy, "_proxy"):
                auth_strategy._proxy = proxy
            logger.debug("Applying auth strategy: %s", type(auth_strategy).__name__)
            auth_strategy.apply(request, headers)
        except Exception as auth_exc:
            safe_msg = redact_body(str(auth_exc), max_length=200) or "unknown error"
            logger.error(
                "Authentication failed (%s): %s — %s",
                type(auth_exc).__name__,
                type(auth_strategy).__name__,
                safe_msg,
            )
            if proxy and (
                "10061" in safe_msg
                or "connection refused" in safe_msg.lower()
                or "econnrefused" in safe_msg.lower()
            ):
                raise RequestError(
                    f"OAuth2 token refresh failed — proxy ({proxy}) is not reachable. "
                    "Please check your proxy settings under Preferences.",
                    details={"proxy": proxy},
                )
            raise RequestError(f"Authentication failed: {safe_msg}")

        auth_headers = {k: headers[k] for k in headers if k not in snapshot}
        if auth_headers:
            logger.debug(
                "Auth applied (%s): %s",
                type(auth_strategy).__name__,
                ", ".join(auth_headers.keys()),
            )
        return auth_headers
