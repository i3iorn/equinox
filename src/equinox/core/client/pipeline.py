from typing import Optional, Callable

from equinox import Request, Response
from equinox.core import EquinoxError, RequestError
from equinox.core.client import logger
from equinox.core.interceptors import InterceptorChain
from equinox.core.redact import redact_body, redact_url


class RequestPipeline:
    def __init__(
        self,
        interceptors: InterceptorChain,
        audit_logger,
        error_handlers,
    ) -> None:
        self._interceptors = interceptors
        self._audit = audit_logger
        self._error_handlers = error_handlers

    # ── Error-handling helpers ────────────────────────────────────────────────

    def _finalize_error(
        self,
        request: Request,
        error: Exception,
        audit_tag: Optional[str],
        log_message: Optional[str],
    ) -> None:
        """Audit, log, and pass *error* through the interceptor chain.

        Raises the interceptor-transformed error when the chain handles it.
        Returns normally when an interceptor suppresses it (returns ``None``),
        letting :meth:`execute` raise a generic "suppressed" ``RequestError``.
        """
        if audit_tag:
            self._audit.log_request(request.method, request.url, error=audit_tag)
        if log_message:
            logger.warning("Request error: %s", log_message)
        processed = self._interceptors.process_error(request, error)
        if processed is not None:
            raise processed

    def _handle_error(
        self,
        request: Request,
        error: Exception,
    ) -> None:
        """Map *error* to a domain error, audit it, and run the interceptor chain.

        Returns normally only when an interceptor suppresses the error.
        """
        if isinstance(error, EquinoxError):
            # Domain error: audit it, let interceptors inspect/transform, then
            # re-raise (or suppress if the interceptor returns None).
            self._finalize_error(
                request, error, type(error).__name__, str(error)
            )
            return

        # Registered handler match
        for exc_type, handler_fn in self._error_handlers:
            if isinstance(error, exc_type):
                logger.debug("Error matched handler for %s", exc_type.__name__)
                kwargs = handler_fn(error, request)
                self._finalize_error(
                    request,
                    kwargs.get("error"),
                    kwargs.get("audit_tag"),
                    kwargs.get("log_message"),
                )
                return

        # Fallback: no registered handler matched
        safe_msg = redact_body(str(error), max_length=500) or ""
        fallback = RequestError(
            f"Request failed: {type(error).__name__}: {safe_msg}",
            details={"error": type(error).__name__},
        )
        self._finalize_error(
            request,
            fallback,
            type(error).__name__,
            f"Unexpected error during request: {type(error).__name__}: {safe_msg}",
        )

    def execute(
        self,
        request: Request,
        dispatch: Callable[[Request], Response],
    ) -> Response:
        logger.debug("RequestPipeline: starting")
        try:
            logger.debug("RequestPipeline: running pre-request interceptors")
            request = self._interceptors.process_request(request)

            response = dispatch(request)

            logger.debug("RequestPipeline: running post-response interceptors")
            response = self._interceptors.process_response(request, response)

            logger.debug("RequestPipeline: logging successful request to audit trail")
            self._audit.log_request(
                request.method, redact_url(request.url), status_code=response.status_code
            )

            logger.debug(
                "RequestPipeline: completed method=%s status=%d elapsed=%.2fs",
                request.method,
                response.status_code,
                response.elapsed,
            )
            return response

        except Exception as exc:
            logger.debug(
                "RequestPipeline: caught exception type=%s message=%s",
                type(exc).__name__,
                str(exc)[:200],
            )
            self._handle_error(request, exc)
            # If error was suppressed by interceptors, raise a generic error
            raise RequestError("Request was suppressed by an interceptor")
