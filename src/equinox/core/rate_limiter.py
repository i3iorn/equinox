"""Simple sliding-window rate limiter used by HTTPClient.

This implementation is thread-safe and stores recent request timestamps in-memory.
It can optionally log a security violation via an AuditLogger when the limit is hit.
"""

import logging
import time
import threading
from typing import List, Optional, Protocol

from equinox.core.exceptions import RateLimitError

logger = logging.getLogger(__name__)

# =========================================================
# Constants
# =========================================================

# max_per_minute values at or below this are treated as "rate limiting disabled".
_LIMIT_DISABLED: int = 0

# Violation type key recognised by AuditLogger.log_security_violation().
_AUDIT_VIOLATION_TYPE: str = "rate_limit"

# =========================================================
# Audit logger protocol
# =========================================================


class _AuditLoggerLike(Protocol):
    """Structural interface required from the optional audit logger.

    Only the single method called by RateLimiter is declared here. This keeps
    the dependency lightweight and avoids importing the concrete AuditLogger,
    preventing circular imports.
    """

    def log_security_violation(
        self,
        violation_type: str,
        details: dict,
        user: Optional[str] = None,
    ) -> None: ...


# =========================================================
# RateLimiter
# =========================================================


class RateLimiter:
    """Thread-safe sliding-window rate limiter.

    Tracks request timestamps in-memory and rejects requests that exceed
    *max_per_minute* within the rolling *window_seconds* window.

    Setting *max_per_minute* to 0 (or any non-positive value) disables rate
    limiting entirely — ``try_acquire()`` returns immediately.

    An optional *audit_logger* receives a security-violation event each time
    the limit is exceeded. Failures to log are swallowed so they never disrupt
    the caller.
    """

    def __init__(
        self,
        max_per_minute: int,
        window_seconds: int = 60,
        audit_logger: Optional[_AuditLoggerLike] = None,
    ) -> None:
        """Create a RateLimiter.

        Args:
            max_per_minute: Maximum requests allowed inside the window.
                            Set to 0 (or negative) to disable rate limiting.
            window_seconds: Length of the sliding time window in seconds
                            (default 60).
            audit_logger:   Optional collaborator that receives a security
                            violation event when the limit is exceeded.  Must
                            implement ``log_security_violation(type, details)``.
        """
        self.max_per_minute = max_per_minute
        self.window_seconds = window_seconds
        self._audit = audit_logger
        self._times: List[float] = []
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return (
            f"RateLimiter("
            f"max_per_minute={self.max_per_minute!r}, "
            f"window_seconds={self.window_seconds!r})"
        )

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def current_count(self) -> int:
        """Number of requests recorded in the current window.

        Thread-safe snapshot; the value may change immediately after reading.
        Useful for monitoring and test assertions.
        """
        with self._lock:
            self._evict_stale(time.time())
            return len(self._times)

    # ── Public API ────────────────────────────────────────────────────────────

    def try_acquire(self) -> None:
        """Record a request timestamp, raising RateLimitError if limit is exceeded.

        This is a no-op when rate limiting is disabled (max_per_minute <= 0).

        Raises:
            RateLimitError: When the number of requests in the current window
                            reaches *max_per_minute*.
        """
        if self.max_per_minute <= _LIMIT_DISABLED:
            return

        with self._lock:
            now = time.time()
            self._evict_stale(now)
            if len(self._times) >= self.max_per_minute:
                self._report_violation()
                raise RateLimitError(
                    f"Rate limit exceeded: {self.max_per_minute} requests per minute"
                )
            self._times.append(now)

    def reset(self) -> None:
        """Clear all recorded timestamps, resetting the limiter to an empty state.

        Primarily useful in tests that need a clean slate between cases without
        creating a fresh instance.
        """
        with self._lock:
            self._times.clear()

    # ── Private helpers ───────────────────────────────────────────────────────

    def _evict_stale(self, now: float) -> None:
        """Remove timestamps that have fallen outside the current window.

        Must be called while ``self._lock`` is held.

        Args:
            now: Current epoch time in seconds.
        """
        cutoff = now - self.window_seconds
        self._times = [t for t in self._times if t > cutoff]

    def _report_violation(self) -> None:
        """Emit a security-violation audit event for the rate-limit breach.

        Failures are logged at DEBUG level and swallowed so they never prevent
        the ``RateLimitError`` from propagating to the caller.

        Must be called while ``self._lock`` is held.
        """
        if self._audit is None:
            return
        try:
            self._audit.log_security_violation(
                _AUDIT_VIOLATION_TYPE,
                {"limit": self.max_per_minute},
            )
        except Exception as exc:
            logger.debug("Failed to log rate-limit audit event: %s", exc)
