"""Concurrency slot guard for the HTTP client."""
from __future__ import annotations

import contextlib
import logging
import threading
from typing import Generator

from equinox.core.exceptions import RequestError

logger = logging.getLogger(__name__)

__all__ = ["ConcurrencyGuard"]


class ConcurrencyGuard:
    """Thread-safe counter that caps the number of concurrent in-flight requests.

    Call :meth:`acquire` before sending a request and :meth:`release` when it
    completes.  Prefer the :meth:`slot` context manager, which guarantees
    release even when the request raises::

        with guard.slot():
            response = client.send(request)

    Args:
        max_concurrent: Maximum number of requests allowed to run at once.
                        Must be a positive integer.

    Raises:
        ValueError: If *max_concurrent* is not a positive integer.
    """

    def __init__(self, max_concurrent: int) -> None:
        if not isinstance(max_concurrent, int) or max_concurrent < 1:
            raise ValueError(
                f"max_concurrent must be a positive integer, got {max_concurrent!r}"
            )
        self._max = max_concurrent
        self._active = 0
        self._lock = threading.Lock()

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def active(self) -> int:
        """Number of slots currently in use."""
        with self._lock:
            return self._active

    @contextlib.contextmanager
    def slot(self) -> Generator[None, None, None]:
        """Context manager that acquires a slot on entry and releases it on exit.

        Raises:
            RequestError: If the concurrency limit is already reached.
        """
        self.acquire()
        try:
            yield
        finally:
            self.release()

    def acquire(self) -> None:
        """Claim one concurrency slot.

        Raises:
            RequestError: If *max_concurrent* slots are already in use.
        """
        with self._lock:
            if self._active >= self._max:
                raise RequestError(
                    f"Too many concurrent requests: {self._active}/{self._max}"
                )
            self._active += 1
            logger.debug("ConcurrencyGuard acquired: active=%d/%d", self._active, self._max)

    def release(self) -> None:
        """Release one previously acquired slot."""
        with self._lock:
            if self._active == 0:
                logger.warning(
                    "ConcurrencyGuard.release() called with no active slots — "
                    "possible acquire/release mismatch"
                )
                return
            self._active -= 1
            logger.debug("ConcurrencyGuard released: active=%d/%d", self._active, self._max)

    # ── Dunder helpers ────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return f"ConcurrencyGuard(active={self._active}, max={self._max})"
