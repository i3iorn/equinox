"""Simple sliding-window rate limiter used by HTTPClient.

This implementation is thread-safe and stores recent request timestamps in-memory.
It can optionally log a security violation via an AuditLogger when the limit is hit.
"""

import time
import threading
from typing import Optional

from equinox.core.exceptions import RateLimitError


class RateLimiter:
    def __init__(self, max_per_minute: int, window_seconds: int = 60, audit_logger: Optional[object] = None):
        self.max_per_minute = max_per_minute
        self.window_seconds = window_seconds
        self._times = []  # list of timestamps
        self._lock = threading.Lock()
        self._audit = audit_logger

    def try_acquire(self) -> None:
        """Attempt to record a request timestamp. Raises RateLimitError if the
        configured per-minute limit is exceeded.
        """
        if self.max_per_minute <= 0:
            return

        with self._lock:
            now = time.time()
            cutoff = now - self.window_seconds
            # keep only recent timestamps
            self._times = [t for t in self._times if t > cutoff]
            if len(self._times) >= self.max_per_minute:
                # Optionally log via audit
                try:
                    if self._audit is not None:
                        self._audit.log_security_violation("rate_limit", {"limit": self.max_per_minute})
                except Exception:
                    pass
                raise RateLimitError(f"Rate limit exceeded: {self.max_per_minute} requests per minute")
            self._times.append(now)

