"""Timeout and HTTP-overload retry policies for the HTTP client."""
from __future__ import annotations

import logging
import threading
import time
from typing import AbstractSet, Callable, Optional

from equinox.core.exceptions import RequestTimeoutError
from equinox.core.request import Response

__all__ = ["RetryPolicy"]

logger = logging.getLogger(__name__)

# ── Module-level defaults ─────────────────────────────────────────────────────

_DEFAULT_RETRYABLE: frozenset[int] = frozenset({429, 503, 504})
_DEFAULT_RETRY_AFTER_CAP: float = 60.0


# ── Policy class ──────────────────────────────────────────────────────────────


class RetryPolicy:
    """Configurable timeout and HTTP-overload retry policy.

    Two independent retry dimensions:

    - *Timeout retries*: retries the callable when a ``RequestTimeoutError``
      is raised, using exponential back-off (1 s, 2 s, 4 s, …).
    - *HTTP overload retries*: after a successful (non-timeout) response whose
      status code is in ``retryable_status_codes``, honours the
      ``Retry-After`` header and retries up to ``http_retries`` more times.

    Args:
        timeout_retries: Maximum number of attempts when the request times
            out.  Clamped to ``≥ 1``.
        http_retries: Maximum number of additional attempts when the server
            responds with a retryable status code.  Clamped to ``≥ 0``.
        retryable_status_codes: Set of HTTP status codes that trigger HTTP
            overload retries.  Defaults to ``{429, 503, 504}``.
        retry_after_cap_seconds: Upper bound (in seconds) for the
            ``Retry-After`` sleep to prevent runaway waits.  Must be
            positive.
        interruptible_sleep: Replacement for ``time.sleep`` — useful in tests
            or GUI event-loop contexts that need a cancellable sleep.

    Raises:
        ValueError: If ``retry_after_cap_seconds`` is not positive.
    """

    # ── Construction ──────────────────────────────────────────────────────────

    def __init__(
        self,
        timeout_retries: int,
        http_retries: int,
        retryable_status_codes: Optional[AbstractSet[int]] = None,
        retry_after_cap_seconds: float = _DEFAULT_RETRY_AFTER_CAP,
        interruptible_sleep: Optional[Callable[[float], None]] = None,
    ) -> None:
        if retry_after_cap_seconds <= 0:
            raise ValueError(
                f"retry_after_cap_seconds must be positive, got {retry_after_cap_seconds!r}"
            )
        self._timeout_retries: int = max(1, int(timeout_retries))
        self._http_retries: int = max(0, int(http_retries))
        self._retryable_status_codes: frozenset[int] = (
            frozenset(retryable_status_codes)
            if retryable_status_codes is not None
            else _DEFAULT_RETRYABLE
        )
        self._retry_after_cap_seconds: float = float(retry_after_cap_seconds)
        self._sleep: Callable[[float], None] = interruptible_sleep or time.sleep
        self._retry_state = threading.local()  # Per-thread retry tracking for UI feedback

    def _get_retry_events(self) -> list:
        events = getattr(self._retry_state, "events", None)
        if events is None:
            events = []
            self._retry_state.events = events
        return events

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def timeout_retries(self) -> int:
        """Maximum number of timeout retry attempts (always ≥ 1)."""
        return self._timeout_retries

    @property
    def http_retries(self) -> int:
        """Maximum number of HTTP overload retry attempts (always ≥ 0)."""
        return self._http_retries

    @property
    def retryable_status_codes(self) -> frozenset[int]:
        """Immutable set of status codes that trigger HTTP overload retries."""
        return self._retryable_status_codes

    def get_retry_summary(self) -> str:
        """Return a human-readable summary of retries that occurred.

        Returns:
            String like "retried 2× after 429" or empty string if no retries.
        """
        retry_events = self._get_retry_events()
        if not retry_events:
            return ""

        # Summarize retry events
        # Format: "retried N× after STATUS_CODE" or "retried N× after timeout"
        http_retries = [e for e in retry_events if e.get("type") == "http"]
        timeout_retries = [e for e in retry_events if e.get("type") == "timeout"]

        parts = []
        if timeout_retries:
            parts.append(f"{len(timeout_retries)}× after timeout")
        if http_retries:
            # Get the most common status code
            statuses = [e.get("status") for e in http_retries if e.get("status")]
            if statuses:
                main_status = statuses[0]
                parts.append(f"{len(http_retries)}× after {main_status}")

        if parts:
            return "retried " + ", ".join(parts)
        return ""

    def clear_retry_events(self) -> None:
        """Clear recorded retry events (called before each execute())."""
        self._retry_state.events = []

    # ── Public API ────────────────────────────────────────────────────────────

    def execute(self, func: Callable[[], Response]) -> Response:
        """Call *func*, retrying on ``RequestTimeoutError`` with exponential back-off.

        Args:
            func: Zero-argument callable that performs the HTTP request.

        Returns:
            The first successful ``Response``.

        Raises:
            RequestTimeoutError: When all timeout attempts are exhausted.
        """
        self.clear_retry_events()  # Reset retry tracking before execution

        for attempt in range(self._timeout_retries):
            logger.debug(
                "RetryPolicy: timeout attempt %d/%d",
                attempt + 1,
                self._timeout_retries,
            )
            try:
                return func()
            except RequestTimeoutError:
                if attempt < self._timeout_retries - 1:
                    wait_seconds = 2 ** attempt  # 1 s, 2 s, 4 s, …
                    # Record retry event for UI
                    self._get_retry_events().append({
                        "type": "timeout",
                        "attempt": attempt + 1,
                        "total_attempts": self._timeout_retries,
                        "wait_seconds": wait_seconds,
                    })
                    self._sleep_backoff(attempt)
                else:
                    logger.error(
                        "Request timed out on final attempt %d/%d, giving up",
                        attempt + 1,
                        self._timeout_retries,
                    )
                    raise

        # Unreachable: __init__ clamps _timeout_retries to ≥ 1 so the loop
        # always executes and either returns or raises above.
        raise AssertionError("RetryPolicy.execute: loop exited without returning")  # pragma: no cover

    def execute_with_http_overload(self, func: Callable[[], Response]) -> Response:
        """Execute with timeout retries followed by HTTP overload retries.

        First calls :meth:`execute` (with timeout back-off).  If the response
        status is retryable, sleeps according to ``Retry-After`` and retries
        up to ``http_retries`` additional times.

        Args:
            func: Zero-argument callable that performs the HTTP request.

        Returns:
            The most recently received ``Response`` (may still carry a
            retryable status if all HTTP overload retries were exhausted).

        Raises:
            RequestTimeoutError: Propagated from :meth:`execute`.
        """
        response = self.execute(func)

        if response.status_code not in self._retryable_status_codes:
            return response

        logger.debug(
            "RetryPolicy: initial response status=%d is retryable; "
            "will attempt up to %d more time(s)",
            response.status_code,
            self._http_retries,
        )

        for attempt in range(self._http_retries):
            retry_after = self._parse_retry_after(response)
            logger.warning(
                "Received %d (attempt %d/%d), retrying after %.1fs",
                response.status_code,
                attempt + 1,
                self._http_retries,
                retry_after,
            )
            # Record retry event for UI
            self._get_retry_events().append({
                "type": "http",
                "attempt": attempt + 1,
                "total_attempts": self._http_retries,
                "status": response.status_code,
                "wait_seconds": retry_after,
            })
            self._sleep(retry_after)
            response = func()
            logger.debug(
                "HTTP overload retry %d/%d completed, status=%d",
                attempt + 1,
                self._http_retries,
                response.status_code,
            )
            if response.status_code not in self._retryable_status_codes:
                break

        return response

    # ── Private helpers ───────────────────────────────────────────────────────

    def _sleep_backoff(self, attempt: int) -> None:
        """Sleep for ``2**attempt`` seconds and emit a warning log."""
        wait_seconds = 2 ** attempt  # 1 s, 2 s, 4 s, …
        logger.warning(
            "Request timed out (attempt %d/%d), retrying in %ds",
            attempt + 1,
            self._timeout_retries,
            wait_seconds,
        )
        self._sleep(wait_seconds)

    def _parse_retry_after(self, response: Response) -> float:
        """Return the ``Retry-After`` delay in seconds, clamped to ``[0, cap]``.

        Falls back to ``1.0 s`` when the header is absent, unparseable, negative,
        or zero — a negative ``Retry-After`` would cause ``time.sleep()`` to raise
        ``ValueError`` and break the retry loop entirely.
        """
        if not response.headers:
            return 1.0
        try:
            retry_after = float(response.headers.get("retry-after", 1))
        except (ValueError, TypeError):
            retry_after = 1.0
        # Guard: negative or zero values are treated as "retry immediately with a
        # brief back-off" so sleep() never receives a non-positive argument.
        if retry_after <= 0:
            logger.info(
                "Retry-After header value %r is non-positive; using 1.0 s fallback",
                retry_after,
            )
            retry_after = 1.0
        return min(retry_after, self._retry_after_cap_seconds)

    # ── Dunder helpers ────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}("
            f"timeout_retries={self._timeout_retries!r}, "
            f"http_retries={self._http_retries!r}, "
            f"retryable_status_codes={self._retryable_status_codes!r}, "
            f"retry_after_cap_seconds={self._retry_after_cap_seconds!r})"
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, RetryPolicy):
            return NotImplemented
        return (
            self._timeout_retries == other._timeout_retries
            and self._http_retries == other._http_retries
            and self._retryable_status_codes == other._retryable_status_codes
            and self._retry_after_cap_seconds == other._retry_after_cap_seconds
        )

    def __hash__(self) -> int:
        return hash((
            self._timeout_retries,
            self._http_retries,
            self._retryable_status_codes,
            self._retry_after_cap_seconds,
        ))
