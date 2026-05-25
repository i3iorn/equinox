"""Request/response pipeline: interceptors, audit logging, and error mapping."""

import inspect
import logging
from collections.abc import Iterable
from typing import Callable

from equinox.core.exceptions import EquinoxError, RequestError
from equinox.core.interceptors.chain import InterceptorChain
from equinox.core.request import Request, Response
from equinox.security import redact_body, redact_url

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Collaborator contracts (structural types)
# ---------------------------------------------------------------------------

try:
    from typing import Protocol, TypedDict
except ImportError:  # pragma: no cover — Python < 3.8 safety net
    from typing_extensions import Protocol, TypedDict


class _AuditLogger(Protocol):
    """Minimal structural interface required of the audit logger."""

    def log_request(
        self,
        method: str,
        url: str,
        *,
        status_code: int | None = None,
        error: str | None = None,
        request_id: str | None = None,
    ) -> None: ...


class _HandlerResultBase(TypedDict):
    """Required field for a registered error-handler result."""

    error: Exception


class _HandlerResult(_HandlerResultBase, total=False):
    """Full shape returned by a registered error-handler callable.

    ``error`` is always required.  ``audit_tag`` and ``log_message`` are
    optional — omit them when the handler has no meaningful values to emit.
    """

    audit_tag: str  # Short tag written to the audit trail.
    log_message: str  # Human-readable description logged at WARNING level.


# A handler maps a raw (exception, request) pair to a structured result.
_ErrorHandlerFn = Callable[[Exception, Request], _HandlerResult]

# Each entry in the registry pairs an exception type with its handler.
_ErrorHandlerEntry = tuple[type[Exception], _ErrorHandlerFn]


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


class RequestPipeline:
    """Orchestrates the three-stage request/response pipeline.

    The pipeline wraps every HTTP dispatch in three stages:

    1. **Pre-request** — :class:`~equinox.core.interceptors.InterceptorChain`
       runs all request interceptors; each may mutate or replace the request.
    2. **Dispatch** — the caller-supplied *dispatch* callable performs the
       actual HTTP round-trip and returns a raw response.
    3. **Post-response** — response interceptors run, then the result is
       written to the audit trail.

    Error handling is centralised here:

    * Known :class:`~equinox.core.exceptions.EquinoxError` subclasses are
      forwarded directly to the interceptor chain for optional transformation.
    * Library-level exceptions (e.g. ``httpx`` transport errors) are matched
      against *error_handlers* and mapped to domain errors first.
    * Any exception not matched by a handler is wrapped in a generic
      :class:`~equinox.core.exceptions.RequestError`.
    """

    __slots__ = ("_interceptors", "_audit", "_error_handlers", "_audit_supports_request_id")

    def __init__(
        self,
        interceptors: InterceptorChain,
        audit_logger: "_AuditLogger",
        error_handlers: "Iterable[_ErrorHandlerEntry]",
    ) -> None:
        self._interceptors = interceptors
        self._audit: _AuditLogger = audit_logger
        # Materialise once so iteration is always O(n) without re-wrapping.
        self._error_handlers: list[_ErrorHandlerEntry] = list(error_handlers)
        self._audit_supports_request_id = self._supports_request_id(audit_logger)

    @staticmethod
    def _supports_request_id(audit_logger: "_AuditLogger") -> bool:
        """Return True when ``audit_logger.log_request`` accepts ``request_id``."""
        try:
            params = inspect.signature(audit_logger.log_request).parameters
        except (TypeError, ValueError):
            return False
        return "request_id" in params or any(
            param.kind == inspect.Parameter.VAR_KEYWORD for param in params.values()
        )

    def _audit_log_request(
        self,
        method: str,
        url: str,
        *,
        status_code: int | None = None,
        error: str | None = None,
        request_id: str | None = None,
    ) -> None:
        """Call the audit logger while remaining compatible with legacy signatures."""
        if self._audit_supports_request_id:
            self._audit.log_request(
                method,
                url,
                status_code=status_code,
                error=error,
                request_id=request_id,
            )
            return
        self._audit.log_request(method, url, status_code=status_code, error=error)

    # ── Error-handling helpers ────────────────────────────────────────────────

    def _emit_error(
        self,
        request: Request,
        error: Exception,
        audit_tag: str | None,
        log_message: str | None,
    ) -> None:
        """Audit, log, and forward *error* through the interceptor chain.

        Raises the interceptor-transformed (or original) error when the chain
        does not suppress it.  Returns normally *only* when an interceptor
        suppresses the error; :meth:`execute` is then responsible for raising
        a sentinel ``RequestError``.
        """
        if audit_tag:
            self._audit_log_request(
                request.method,
                request.url,
                error=audit_tag,
                request_id=request.correlation_id,
            )
        if log_message:
            logger.warning(
                "Request error: %s",
                log_message,
                extra={
                    "error_type": type(error).__name__,
                    "error_message": str(error),
                    "request_method": request.method,
                    "request_url": redact_url(request.url),
                    "request_id": request.correlation_id,
                    "error_details": getattr(error, "details", None),
                    "audit_tag": audit_tag,
                },
            )

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
        # Domain errors already carry structured context — forward directly.
        if isinstance(error, EquinoxError):
            logger.debug(
                "Handling domain error: %s",
                type(error).__name__,
                extra={
                    "error_type": type(error).__name__,
                    "error_message": str(error),
                    "request_url": redact_url(request.url),
                    "request_method": request.method,
                    "request_id": request.correlation_id,
                    "error_details": getattr(error, "details", None),
                },
            )
            self._emit_error(request, error, type(error).__name__, str(error))
            return  # Reached only when an interceptor suppressed the error.

        # Walk the registered handlers in priority order.
        for exc_type, handler_fn in self._error_handlers:
            if isinstance(error, exc_type):
                logger.debug(
                    "Error matched handler for %s",
                    exc_type.__name__,
                    extra={
                        "error_type": type(error).__name__,
                        "handler_type": exc_type.__name__,
                        "request_url": redact_url(request.url),
                        "request_id": request.correlation_id,
                    },
                )
                result: _HandlerResult = handler_fn(error, request)
                mapped_error = result["error"]
                self._emit_error(
                    request,
                    mapped_error,
                    result.get("audit_tag"),
                    result.get("log_message"),
                )
                return  # Reached only when an interceptor suppressed the error.

        # No handler matched — wrap in a generic RequestError.
        safe_msg = redact_body(str(error), max_length=500) or ""
        exc_name = type(error).__name__
        logger.warning(
            "No error handler matched: wrapping %s as RequestError",
            exc_name,
            extra={
                "error_type": exc_name,
                "error_message": safe_msg,
                "request_url": redact_url(request.url),
                "request_id": request.correlation_id,
                "error_details": getattr(error, "details", None),
            },
        )
        fallback = RequestError(
            f"Request failed: {exc_name}: {safe_msg}",
            details={"error": exc_name},
        )
        self._emit_error(
            request,
            fallback,
            exc_name,
            f"Unexpected error during request: {exc_name}: {safe_msg}",
        )

    def execute(
        self,
        request: Request,
        dispatch: Callable[[Request], Response],
    ) -> Response:
        """Run the full pipeline for a single request/response cycle.

        Args:
            request:  The outbound :class:`~equinox.core.request.Request`.
            dispatch: Callable that performs the actual HTTP round-trip and
                      returns a :class:`~equinox.core.request.Response`.

        Returns:
            The processed :class:`~equinox.core.request.Response`.

        Raises:
            :class:`~equinox.core.exceptions.RequestError`: On any transport or
                processing failure not suppressed by an error interceptor.
        """
        logger.debug(
            "RequestPipeline.execute: starting",
            extra={
                "request_method": request.method,
                "request_url": redact_url(request.url),
                "request_id": request.correlation_id,
            },
        )
        try:
            logger.debug("RequestPipeline.execute: running pre-request interceptors")
            request = self._interceptors.process_request(request)

            response = dispatch(request)

            logger.debug("RequestPipeline.execute: running post-response interceptors")
            response = self._interceptors.process_response(request, response)

            self._audit_log_request(
                request.method,
                (redact_url(request.url or "") or ""),
                status_code=response.status_code,
                request_id=request.correlation_id,
            )
            logger.debug(
                "RequestPipeline.execute: completed method=%s status=%d elapsed=%.2fs",
                request.method,
                response.status_code,
                response.elapsed,
                extra={
                    "request_method": request.method,
                    "status_code": response.status_code,
                    "elapsed_seconds": response.elapsed,
                    "request_url": redact_url(request.url),
                    "request_id": request.correlation_id,
                },
            )
            return response

        except Exception as exc:
            logger.debug(
                "RequestPipeline.execute: caught %s — %s",
                type(exc).__name__,
                (redact_body(str(exc), max_length=200) or ""),
                extra={
                    "error_type": type(exc).__name__,
                    "error_message": redact_body(str(exc), max_length=200),
                    "request_url": redact_url(request.url),
                    "request_method": request.method,
                    "request_id": request.correlation_id,
                },
            )
            self._handle_error(request, exc)
            # _handle_error returned normally: an interceptor suppressed the error.
            raise RequestError("Request suppressed by an error interceptor") from exc
