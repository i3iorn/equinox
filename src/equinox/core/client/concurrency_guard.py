"""Concurrency slot guard for the HTTP client."""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from collections.abc import Generator

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
            raise ValueError(f"max_concurrent must be a positive integer, got {max_concurrent!r}")
        self._max = max_concurrent
        self._active = 0
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def active(self) -> int:
        """Number of slots currently in use.

        Returns:
            Current number of active requests (0 to max_concurrent).
        """
        with self._lock:
            return self._active

    @contextlib.contextmanager
    def slot(self, timeout: float | None = None) -> Generator[None, None, None]:
        """Context manager that acquires a slot on entry and releases it on exit.

        Args:
            timeout: Maximum seconds to wait for a slot to become available.
                     If None, raises immediately if limit is reached.

        Raises:
            RequestError: If timeout expires before acquiring a slot.
        """
        self.acquire(timeout=timeout)
        try:
            yield
        finally:
            self.release()

    def acquire(self, timeout: float | None = None) -> None:
        """Claim one concurrency slot.

        Args:
            timeout: Maximum seconds to wait for a slot. If None, raises immediately
                     if the limit is reached. If 0, waits indefinitely.

        Raises:
            RequestError: If unable to acquire a slot within the timeout.
        """
        with self._condition:
            # Wait for slot to become available (with optional timeout)
            start = time.perf_counter() if timeout is not None else None

            while self._active >= self._max:
                if timeout == 0:
                    # Timeout=0 means wait indefinitely (classic threading convention)
                    self._condition.wait()
                elif timeout is not None:
                    if start is None:
                        start = time.perf_counter()
                    elapsed = time.perf_counter() - start
                    remaining = timeout - elapsed
                    if remaining <= 0:
                        raise RequestError(
                            f"Failed to acquire concurrency slot: "
                            f"limit reached ({self._active}/{self._max}) "
                            f"and timeout expired after {timeout}s"
                        )
                    self._condition.wait(timeout=remaining)
                else:
                    # No timeout: fail immediately
                    raise RequestError(f"Too many concurrent requests: {self._active}/{self._max}")

            self._active += 1
            logger.debug("ConcurrencyGuard acquired: active=%d/%d", self._active, self._max)

    def release(self) -> None:
        """Release one previously acquired slot.

        Raises:
            RuntimeError: If called when no slots are active (acquire/release mismatch).
        """
        with self._condition:
            if self._active == 0:
                raise RuntimeError(
                    "ConcurrencyGuard.release() called with no active slots — "
                    "acquire/release mismatch detected"
                )
            self._active -= 1
            self._condition.notify_all()  # Wake waiting threads
            logger.debug("ConcurrencyGuard released: active=%d/%d", self._active, self._max)

    # ── Dunder helpers ────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        """Return developer-friendly representation."""
        return f"ConcurrencyGuard(active={self._active}, max={self._max})"
