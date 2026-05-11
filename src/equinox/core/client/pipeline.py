"""Request/response pipeline: interceptors, audit logging, and error mapping."""
import logging
from typing import Callable, Iterable, List, Optional, Tuple, Type

from equinox.core.request import Request, Response
from equinox.core.exceptions import EquinoxError, RequestError
from equinox.core.interceptors.chain import InterceptorChain
from equinox.security import redact_body, redact_url

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Collaborator contracts (structural types)
# ---------------------------------------------------------------------------

try:
    from typing import Protocol, TypedDict
except ImportError:  # pragma: no cover — Python < 3.8 safety net
    from typing_extensions import Protocol, TypedDict  # type: ignore[assignment]


class _AuditLogger(Protocol):
    """Minimal structural interface required of the audit logger."""

    def log_request(
        self,
        method: str,
        url: str,
        *,
        status_code: Optional[int] = None,
        error: Optional[str] = None,
    ) -> None: ...


class _HandlerResultBase(TypedDict):
    """Required field for a registered error-handler result."""

    error: Exception


class _HandlerResult(_HandlerResultBase, total=False):
    """Full shape returned by a registered error-handler callable.

    ``error`` is always required.  ``audit_tag`` and ``log_message`` are
    optional — omit them when the handler has no meaningful values to emit.
    """

    audit_tag: str   # Short tag written to the audit trail.
    log_message: str  # Human-readable description logged at WARNING level.


# A handler maps a raw (exception, request) pair to a structured result.
_ErrorHandlerFn = Callable[[Exception, Request], _HandlerResult]

# Each entry in the registry pairs an exception type with its handler.
_ErrorHandlerEntry = Tuple[Type[Exception], _ErrorHandlerFn]


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

    __slots__ = ("_interceptors", "_audit", "_error_handlers")

    def __init__(
        self,
        interceptors: InterceptorChain,
        audit_logger: "_AuditLogger",
        error_handlers: "Iterable[_ErrorHandlerEntry]",
    ) -> None:
        self._interceptors = interceptors
        self._audit: "_AuditLogger" = audit_logger
        # Materialise once so iteration is always O(n) without re-wrapping.
        self._error_handlers: "List[_ErrorHandlerEntry]" = list(error_handlers)

    # ── Error-handling helpers ────────────────────────────────────────────────

    def _emit_error(
        self,
        request: Request,
        error: Exception,
        audit_tag: Optional[str],
        log_message: Optional[str],
    ) -> None:
        """Audit, log, and forward *error* through the interceptor chain.

        Raises the interceptor-transformed (or original) error when the chain
        does not suppress it.  Returns normally *only* when an interceptor
        suppresses the error; :meth:`execute` is then responsible for raising
        a sentinel ``RequestError``.
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
        # Domain errors already carry structured context — forward directly.
        if isinstance(error, EquinoxError):
            self._emit_error(request, error, type(error).__name__, str(error))
            return  # Reached only when an interceptor suppressed the error.

        # Walk the registered handlers in priority order.
        for exc_type, handler_fn in self._error_handlers:
            if isinstance(error, exc_type):
                logger.debug("Error matched handler for %s", exc_type.__name__)
                result: "_HandlerResult" = handler_fn(error, request)
                self._emit_error(
                    request,
                    result.get("error"),
                    result.get("audit_tag"),
                    result.get("log_message"),
                )
                return  # Reached only when an interceptor suppressed the error.

        # No handler matched — wrap in a generic RequestError.
        safe_msg = redact_body(str(error), max_length=500) or ""
        exc_name = type(error).__name__
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
        logger.debug("RequestPipeline.execute: starting")
        try:
            logger.debug("RequestPipeline.execute: running pre-request interceptors")
            request = self._interceptors.process_request(request)

            response = dispatch(request)

            logger.debug("RequestPipeline.execute: running post-response interceptors")
            response = self._interceptors.process_response(request, response)

            self._audit.log_request(
                request.method,
                (redact_url(request.url or "") or ""),
                status_code=response.status_code,
            )
            logger.debug(
                "RequestPipeline.execute: completed method=%s status=%d elapsed=%.2fs",
                request.method,
                response.status_code,
                response.elapsed,
            )
            return response

        except Exception as exc:
            logger.debug(
                "RequestPipeline.execute: caught %s — %s",
                type(exc).__name__,
                (redact_body(str(exc), max_length=200) or ""),
            )
            self._handle_error(request, exc)
            # _handle_error returned normally: an interceptor suppressed the error.
            raise RequestError("Request suppressed by an error interceptor")
