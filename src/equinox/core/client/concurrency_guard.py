"""Concurrency slot guard for the HTTP client."""
import logging
import threading

from equinox.core.exceptions import RequestError

logger = logging.getLogger(__name__)


class ConcurrencyGuard:
    def __init__(self, max_concurrent: int) -> None:
        self._max = max_concurrent
        self._active = 0
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            if self._active >= self._max:
                raise RequestError(
                    f"Too many concurrent requests: {self._active}/{self._max}"
                )
            self._active += 1
            logger.debug("ConcurrencyGuard acquired: active=%d", self._active)

    def release(self) -> None:
        with self._lock:
            self._active = max(0, self._active - 1)
            logger.debug("ConcurrencyGuard released: active=%d", self._active)
