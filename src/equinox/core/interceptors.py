"""
Request/Response interceptor system and structured logging.

Features:
- Explicit interceptor control flow
- Safe mutation via context helpers
- Structured logging abstraction
- Robust body handling
- Extensible + future-proof design
"""

import logging
import json
from typing import Optional, List, Any, Dict, Generic, TypeVar
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from equinox.core.time import utc_now
from equinox.core.request import Request, Response
from equinox.core.redact import redact_headers, redact_body, redact_url

logger = logging.getLogger(__name__)


# =========================
# Interceptor Core Types
# =========================

T = TypeVar("T")


class InterceptorAction(Enum):
    CONTINUE = "continue"
    STOP = "stop"
    REPLACE = "replace"
    SUPPRESS = "suppress"


@dataclass
class InterceptorResult(Generic[T]):
    action: InterceptorAction
    value: Optional[T] = None

    @classmethod
    def continue_(cls):
        return cls(InterceptorAction.CONTINUE)

    @classmethod
    def stop(cls):
        return cls(InterceptorAction.STOP)

    @classmethod
    def replace(cls, value: T):
        return cls(InterceptorAction.REPLACE, value)

    @classmethod
    def suppress(cls):
        return cls(InterceptorAction.SUPPRESS)


# =========================
# Context
# =========================

@dataclass
class InterceptorContext:
    request: Request
    response: Optional[Response] = None
    error: Optional[Exception] = None
    timestamp: datetime = field(default_factory=utc_now)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def replace_request(self, request: Request) -> None:
        self.request = request

    def replace_response(self, response: Response) -> None:
        self.response = response

    def replace_error(self, error: Exception) -> None:
        self.error = error


# =========================
# Base Interceptors
# =========================

class RequestInterceptor:
    def can_intercept(self, request: Request) -> bool:
        """Return True only when the provided object is a Request instance.

        Defensive runtime type-checking prevents interceptors from being
        invoked with unexpected values (e.g. None or wrong types). Subclasses
        may override to implement more specific guards.
        """
        return isinstance(request, Request)

    def intercept(self, context: InterceptorContext) -> InterceptorResult[Request]:
        return InterceptorResult.continue_()


class ResponseInterceptor:
    def can_intercept(self, response: Response) -> bool:
        """Return True only when the provided object is a Response instance.

        The interceptor chain may pass an Optional[Response]; guard against
        None and non-Response values here so concrete interceptors can rely
        on a valid response object in their intercept() implementation.
        """
        return isinstance(response, Response)

    def intercept(self, context: InterceptorContext) -> InterceptorResult[Response]:
        return InterceptorResult.continue_()


class ErrorInterceptor:
    def can_intercept(self, error: Exception, request: Request) -> bool:
        """Return True only when error is an Exception and request is a Request.

        This avoids accidentally handling non-exception values or invalid
        request objects. Concrete implementations can extend this check.
        """
        return isinstance(error, Exception) and isinstance(request, Request)

    def intercept(self, context: InterceptorContext) -> InterceptorResult[Exception]:
        return InterceptorResult.continue_()


# =========================
# Interceptor Chain
# =========================

class InterceptorChain:
    def __init__(self):
        self.request_interceptors: List[RequestInterceptor] = []
        self.response_interceptors: List[ResponseInterceptor] = []
        self.error_interceptors: List[ErrorInterceptor] = []

    def add_request_interceptor(self, interceptor: RequestInterceptor) -> None:
        self.request_interceptors.append(interceptor)
        logger.debug("Request interceptor registered: %s", type(interceptor).__name__)

    def add_response_interceptor(self, interceptor: ResponseInterceptor) -> None:
        self.response_interceptors.append(interceptor)
        logger.debug("Response interceptor registered: %s", type(interceptor).__name__)

    def add_error_interceptor(self, interceptor: ErrorInterceptor) -> None:
        self.error_interceptors.append(interceptor)
        logger.debug("Error interceptor registered: %s", type(interceptor).__name__)

    # -------- Request --------

    def process_request(self, request: Request) -> Request:
        context = InterceptorContext(request=request)
        logger.debug("Processing request through %d interceptor(s)", len(self.request_interceptors),
                     extra={"method": request.method, "url": redact_url(request.url)})

        for interceptor in self.request_interceptors:
            if not interceptor.can_intercept(context.request):
                continue

            result = interceptor.intercept(context)
            logger.debug("Request interceptor %s returned %s",
                         type(interceptor).__name__, result.action.value)

            if result.action == InterceptorAction.REPLACE:
                context.replace_request(result.value)

            elif result.action == InterceptorAction.STOP:
                logger.debug("Request interceptor chain stopped by %s", type(interceptor).__name__)
                break

        return context.request

    # -------- Response --------

    def process_response(self, request: Request, response: Response) -> Response:
        context = InterceptorContext(request=request, response=response)
        logger.debug("Processing response through %d interceptor(s)", len(self.response_interceptors),
                     extra={"status_code": response.status_code})

        for interceptor in self.response_interceptors:
            if not interceptor.can_intercept(context.response):
                continue

            result = interceptor.intercept(context)
            logger.debug("Response interceptor %s returned %s",
                         type(interceptor).__name__, result.action.value)

            if result.action == InterceptorAction.REPLACE:
                context.replace_response(result.value)

            elif result.action == InterceptorAction.STOP:
                logger.debug("Response interceptor chain stopped by %s", type(interceptor).__name__)
                break

        return context.response

    # -------- Error --------

    def process_error(self, request: Request, error: Exception) -> Optional[Exception]:
        context = InterceptorContext(request=request, error=error)
        logger.debug("Processing error through %d interceptor(s): %s",
                     len(self.error_interceptors), type(error).__name__)

        for interceptor in self.error_interceptors:
            if not interceptor.can_intercept(context.error, context.request):
                continue

            result = interceptor.intercept(context)
            logger.debug("Error interceptor %s returned %s",
                         type(interceptor).__name__, result.action.value)

            if result.action == InterceptorAction.SUPPRESS:
                logger.debug("Error suppressed by %s", type(interceptor).__name__)
                return None

            if result.action == InterceptorAction.REPLACE:
                context.replace_error(result.value)

            elif result.action == InterceptorAction.STOP:
                logger.debug("Error interceptor chain stopped by %s", type(interceptor).__name__)
                break

        return context.error


# =========================
# Logging Utilities
# =========================

def _safe_body_preview(body: Any, limit: int = 1000) -> str:
    if body is None:
        return ""

    if isinstance(body, (bytes, bytearray)):
        return body[:limit].decode(errors="replace")

    return str(body)[:limit]


class StructuredLogger:
    def __init__(self, logger: logging.Logger):
        self.logger = logger

    def log(self, level: int, event: str, payload: Dict[str, Any]) -> None:
        data = {
            "event": event,
            "timestamp": utc_now().isoformat(),
            **payload,
        }

        self.logger.log(level, json.dumps(data, ensure_ascii=False), extra=data)


# =========================
# Request/Response Logger
# =========================

class RequestResponseLogger:
    def __init__(self, logger_name: str = "equinox.requests"):
        self._logger = StructuredLogger(logging.getLogger(logger_name))

    def log_request(
        self,
        request: Request,
        level: int = logging.INFO,
        include_body: bool = False,
    ) -> None:
        payload = {
            "method": request.method,
            "url": redact_url(request.url),
            "headers": redact_headers(request.headers or {}),
            "params": dict(request.params or {}),
            "timeout": request.timeout,
            "verify_ssl": request.verify_ssl,
        }

        if include_body:
            payload["body"] = redact_body(
                _safe_body_preview(request.body),
                max_length=1000,
            )

        self._logger.log(level, "request_sent", payload)

    def log_response(
        self,
        request: Request,
        response: Response,
        elapsed_time: float,
        level: int = logging.INFO,
        include_body: bool = False,
    ) -> None:
        payload = {
            "method": request.method,
            "url": redact_url(request.url),
            "status_code": response.status_code,
            "reason": response.reason,
            "elapsed_time_seconds": elapsed_time,
            "headers": redact_headers(dict(response.headers or {})),
        }

        if include_body:
            payload["body"] = redact_body(
                _safe_body_preview(response.body),
                max_length=1000,
            )

        self._logger.log(level, "response_received", payload)

    def log_error(
        self,
        request: Request,
        error: Exception,
        level: int = logging.ERROR,
    ) -> None:
        payload = {
            "method": request.method,
            "url": redact_url(request.url),
            "error_type": type(error).__name__,
            "error_message": redact_body(str(error), max_length=500),
        }

        self._logger.log(level, "request_failed", payload)


# =========================
# Built-in Logging Interceptors
# =========================

class LoggingRequestInterceptor(RequestInterceptor):
    def __init__(self, logger: Optional[RequestResponseLogger] = None):
        self.logger = logger or RequestResponseLogger()

    def intercept(self, context: InterceptorContext) -> InterceptorResult[Request]:
        self.logger.log_request(context.request, include_body=True)
        return InterceptorResult.continue_()


class LoggingResponseInterceptor(ResponseInterceptor):
    def __init__(self, logger: Optional[RequestResponseLogger] = None):
        self.logger = logger or RequestResponseLogger()

    def intercept(self, context: InterceptorContext) -> InterceptorResult[Response]:
        elapsed = context.response.elapsed if context.response else 0
        self.logger.log_response(
            context.request,
            context.response,
            elapsed,
            include_body=True,
        )
        return InterceptorResult.continue_()


class LoggingErrorInterceptor(ErrorInterceptor):
    def __init__(self, logger: Optional[RequestResponseLogger] = None):
        self.logger = logger or RequestResponseLogger()

    def intercept(self, context: InterceptorContext) -> InterceptorResult[Exception]:
        self.logger.log_error(context.request, context.error)
        return InterceptorResult.continue_()