import time
from typing import Optional, Callable

from equinox import Response
from equinox.core import RequestTimeoutError
from equinox.core.client import logger


class RetryPolicy:
    def __init__(
        self,
        timeout_retries: int,
        http_retries: int,
        retryable_status_codes: Optional[set] = None,
        retry_after_cap_seconds: float = 60.0,
        interruptible_sleep: Optional[Callable[[float], None]] = None,
    ) -> None:
        self._timeout_retries = max(1, timeout_retries)
        self._http_retries = max(0, http_retries)
        self._retryable_status_codes = retryable_status_codes or {429, 503, 504}
        self._retry_after_cap_seconds = retry_after_cap_seconds
        self._sleep = interruptible_sleep or time.sleep

    def _sleep_backoff(self, attempt: int) -> None:
        wait_seconds = 2 ** attempt  # 1s, 2s, 4s, ...
        logger.warning(
            "Request timed out (attempt %d/%d), retrying in %ds",
            attempt + 1,
            self._timeout_retries,
            wait_seconds,
        )
        self._sleep(wait_seconds)

    def _parse_retry_after(self, response: Response) -> float:
        if not response.headers:
            return 1.0
        try:
            retry_after = float(response.headers.get("retry-after", 1))
        except (ValueError, TypeError):
            retry_after = 1.0
        return min(retry_after, self._retry_after_cap_seconds)

    def execute(self, func: Callable[[], Response]) -> Response:
        # Timeout retries
        for attempt in range(self._timeout_retries):
            try:
                logger.debug(
                    "RetryPolicy: timeout attempt %d/%d",
                    attempt + 1,
                    self._timeout_retries,
                )
                return func()
            except RequestTimeoutError:
                if attempt < self._timeout_retries - 1:
                    self._sleep_backoff(attempt)
                else:
                    logger.error(
                        "Request timed out on final attempt %d/%d, giving up",
                        attempt + 1,
                        self._timeout_retries,
                    )
                    raise
        # This line is only reached when _timeout_retries == 0 (misconfiguration).
        raise RuntimeError("RetryPolicy: _timeout_retries must be >= 1")

    def execute_with_http_overload(self, func: Callable[[], Response]) -> Response:
        """Execute with timeout retries + HTTP overload retries."""
        response = self.execute(func)

        if response.status_code not in self._retryable_status_codes:
            return response

        logger.debug(
            "RetryPolicy: HTTP overload status=%d (retryable=%s)",
            response.status_code,
            response.status_code in self._retryable_status_codes,
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
            self._sleep(retry_after)
            response = func()
            logger.debug(
                "HTTP overload retry attempt %d/%d completed, status=%d",
                attempt + 1,
                self._http_retries,
                response.status_code,
            )
            if response.status_code not in self._retryable_status_codes:
                break

        return response
