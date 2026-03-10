"""Request/Response interceptor system and logging API.

Provides middleware-like interceptors for request/response lifecycle
and comprehensive structured logging for debugging and auditing.
"""

import logging
import json
from typing import Optional, List, Any, Dict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from equinox.core.request import Request, Response
from equinox.core.redact import redact_headers, redact_body, redact_url


class InterceptorType(Enum):
    """Types of interceptors."""
    REQUEST = "request"
    RESPONSE = "response"
    ERROR = "error"


@dataclass
class InterceptorContext:
    """Context passed to interceptors."""
    request: Request
    response: Optional[Response] = None
    error: Optional[Exception] = None
    timestamp: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now(timezone.utc).replace(tzinfo=None)


class RequestInterceptor:
    """Base class for request interceptors.

    Interceptors can inspect and modify requests before they're sent.
    """

    def can_intercept(self, request: Request) -> bool:
        """Check if this interceptor should handle the request.

        Args:
            request: Request to check

        Returns:
            True if interceptor should handle this request
        """
        return True

    def intercept(self, context: InterceptorContext) -> Optional[Request]:
        """Intercept and potentially modify a request.

        Args:
            context: Interceptor context with request

        Returns:
            Modified request or None to use original
        """
        return None


class ResponseInterceptor:
    """Base class for response interceptors.

    Interceptors can inspect and modify responses after they're received.
    """

    def can_intercept(self, response: Response) -> bool:
        """Check if this interceptor should handle the response.

        Args:
            response: Response to check

        Returns:
            True if interceptor should handle this response
        """
        return True

    def intercept(self, context: InterceptorContext) -> Optional[Response]:
        """Intercept and potentially modify a response.

        Args:
            context: Interceptor context with response

        Returns:
            Modified response or None to use original
        """
        return None


class ErrorInterceptor:
    """Base class for error interceptors.

    Interceptors can handle and potentially suppress errors.
    """

    def can_intercept(self, error: Exception, request: Request) -> bool:
        """Check if this interceptor should handle the error.

        Args:
            error: Exception that occurred
            request: Request that caused the error

        Returns:
            True if interceptor should handle this error
        """
        return True

    def intercept(self, context: InterceptorContext) -> Optional[Exception]:
        """Intercept and potentially suppress an error.

        Args:
            context: Interceptor context with error

        Returns:
            Exception to re-raise, or None to suppress
        """
        return context.error


class InterceptorChain:
    """Manages a chain of interceptors."""

    def __init__(self):
        """Initialize empty interceptor chain."""
        self.request_interceptors: List[RequestInterceptor] = []
        self.response_interceptors: List[ResponseInterceptor] = []
        self.error_interceptors: List[ErrorInterceptor] = []

    def add_request_interceptor(self, interceptor: RequestInterceptor) -> None:
        """Add a request interceptor.

        Args:
            interceptor: Request interceptor to add
        """
        self.request_interceptors.append(interceptor)

    def add_response_interceptor(self, interceptor: ResponseInterceptor) -> None:
        """Add a response interceptor.

        Args:
            interceptor: Response interceptor to add
        """
        self.response_interceptors.append(interceptor)

    def add_error_interceptor(self, interceptor: ErrorInterceptor) -> None:
        """Add an error interceptor.

        Args:
            interceptor: Error interceptor to add
        """
        self.error_interceptors.append(interceptor)

    def process_request(self, request: Request) -> Request:
        """Process request through all interceptors.

        Args:
            request: Request to process

        Returns:
            Processed request
        """
        context = InterceptorContext(request=request)

        for interceptor in self.request_interceptors:
            if not interceptor.can_intercept(context.request):
                continue

            modified = interceptor.intercept(context)
            if modified is not None:
                context.request = modified

        return context.request

    def process_response(self, request: Request, response: Response) -> Response:
        """Process response through all interceptors.

        Args:
            request: Original request
            response: Response to process

        Returns:
            Processed response
        """
        context = InterceptorContext(request=request, response=response)

        for interceptor in self.response_interceptors:
            if not interceptor.can_intercept(context.response):
                continue

            modified = interceptor.intercept(context)
            if modified is not None:
                context.response = modified

        return context.response

    def process_error(self, request: Request, error: Exception) -> Optional[Exception]:
        """Process error through all interceptors.

        Args:
            request: Request that caused error
            error: Exception to process

        Returns:
            Exception to re-raise, or None to suppress
        """
        context = InterceptorContext(request=request, error=error)

        for interceptor in self.error_interceptors:
            if not interceptor.can_intercept(context.error, context.request):
                continue

            result = interceptor.intercept(context)
            if result is None:
                # Error was suppressed
                return None
            context.error = result

        return context.error


class RequestResponseLogger:
    """Structured logging for requests and responses."""

    def __init__(self, logger_name: str = "equinox.requests"):
        """Initialize request/response logger.

        Args:
            logger_name: Logger name
        """
        self.logger = logging.getLogger(logger_name)

    def log_request(
        self,
        request: Request,
        level: int = logging.INFO,
        include_body: bool = False,
    ) -> None:
        """Log a request.

        Args:
            request: Request to log
            level: Logging level
            include_body: Whether to include request body
        """
        safe_headers = redact_headers(request.headers or {})
        log_data = {
            "event": "request_sent",
            "method": request.method,
            "url": redact_url(request.url),
            "headers": safe_headers,
            "params": dict(request.params) if request.params else {},
            "timeout": request.timeout,
            "verify_ssl": request.verify_ssl,
            "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        }

        if include_body and request.body:
            log_data["body"] = redact_body(request.body[:1000], max_length=1000)

        self.logger.log(level, json.dumps(log_data))

    def log_response(
        self,
        request: Request,
        response: Response,
        elapsed_time: float,
        level: int = logging.INFO,
        include_body: bool = False,
    ) -> None:
        """Log a response.

        Args:
            request: Original request
            response: Response received
            elapsed_time: Time taken for request
            level: Logging level
            include_body: Whether to include response body
        """
        log_data = {
            "event": "response_received",
            "method": request.method,
            "url": redact_url(request.url),
            "status_code": response.status_code,
            "reason": response.reason,
            "elapsed_time_seconds": elapsed_time,
            "headers": redact_headers(dict(response.headers) if response.headers else {}),
            "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        }

        if include_body and response.body:
            body_preview = response.body[:1000] if isinstance(response.body, str) else str(response.body)[:1000]
            log_data["body"] = redact_body(body_preview, max_length=1000)

        self.logger.log(level, json.dumps(log_data))

    def log_error(
        self,
        request: Request,
        error: Exception,
        level: int = logging.ERROR,
    ) -> None:
        """Log a request error.

        Args:
            request: Request that failed
            error: Exception that occurred
            level: Logging level
        """
        error_msg = str(error)
        # Strip potential credential fragments from error messages
        error_msg = redact_body(error_msg, max_length=500)
        log_data = {
            "event": "request_failed",
            "method": request.method,
            "url": redact_url(request.url),
            "error_type": type(error).__name__,
            "error_message": error_msg,
            "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        }

        self.logger.log(level, json.dumps(log_data))


class LoggingRequestInterceptor(RequestInterceptor):
    """Built-in interceptor that logs all requests."""

    def __init__(self, logger: Optional[RequestResponseLogger] = None):
        """Initialize logging interceptor.

        Args:
            logger: Logger to use (creates new if None)
        """
        self.logger = logger or RequestResponseLogger()

    def intercept(self, context: InterceptorContext) -> Optional[Request]:
        """Log the request."""
        self.logger.log_request(context.request, include_body=True)
        return None


class LoggingResponseInterceptor(ResponseInterceptor):
    """Built-in interceptor that logs all responses."""

    def __init__(self, logger: Optional[RequestResponseLogger] = None):
        """Initialize logging interceptor.

        Args:
            logger: Logger to use (creates new if None)
        """
        self.logger = logger or RequestResponseLogger()

    def intercept(self, context: InterceptorContext) -> Optional[Response]:
        """Log the response."""
        elapsed = context.response.elapsed if context.response else 0
        self.logger.log_response(context.request, context.response, elapsed, include_body=True)
        return None


class LoggingErrorInterceptor(ErrorInterceptor):
    """Built-in interceptor that logs all errors."""

    def __init__(self, logger: Optional[RequestResponseLogger] = None):
        """Initialize logging interceptor.

        Args:
            logger: Logger to use (creates new if None)
        """
        self.logger = logger or RequestResponseLogger()

    def intercept(self, context: InterceptorContext) -> Optional[Exception]:
        """Log the error and re-raise it."""
        self.logger.log_error(context.request, context.error)
        return context.error

