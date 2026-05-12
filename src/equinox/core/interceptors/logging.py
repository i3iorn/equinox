import logging
from typing import Dict, Any, Optional

from equinox.core.request import Response, Request
from equinox.core.interceptors._base import RequestInterceptor, ResponseInterceptor, \
    ErrorInterceptor, InterceptorContext, InterceptorResult
from equinox.core.logging_payload import request_payload, response_payload, \
    error_payload

from equinox.core.time import utc_now


class StructuredLogger:
    def __init__(self, logger: logging.Logger):
        self.logger = logger

    def log(self, level: int, event: str, payload: Dict[str, Any]) -> None:
        data = {
            "event": event,
            "timestamp": utc_now().isoformat(),
            **payload,
        }

        self.logger.log(level, event, extra=data)


class RequestResponseLogger:
    def __init__(self, logger_name: str = "equinox.requests"):
        self._logger = StructuredLogger(logging.getLogger(logger_name))

    def log_request(
        self,
        request_or_payload: Any,
        level: int = logging.INFO,
        include_body: bool = False,
    ) -> None:
        if isinstance(request_or_payload, dict):
            payload = request_or_payload
        else:
            payload = request_payload(request_or_payload, include_body=include_body)

        self._logger.log(level, "request_sent", payload)

    def log_response(
        self,
        request_or_payload: Any,
        response: Optional[Response] = None,
        elapsed_time: float = 0.0,
        level: int = logging.INFO,
        include_body: bool = False,
    ) -> None:
        if isinstance(request_or_payload, dict):
            payload = request_or_payload
        else:
            payload = response_payload(
                request_or_payload, response, elapsed_time, include_body=include_body
            )

        self._logger.log(level, "response_received", payload)

    def log_error(
        self,
        request_or_payload: Any,
        error: Optional[Exception] = None,
        level: int = logging.ERROR,
    ) -> None:
        if isinstance(request_or_payload, dict):
            payload = request_or_payload
        else:
            payload = error_payload(request_or_payload, error)

        self._logger.log(level, "request_failed", payload)


class LoggingRequestInterceptor(RequestInterceptor):
    def __init__(self, logger: Optional[RequestResponseLogger] = None):
        self.logger = logger or RequestResponseLogger()

    def intercept(self, context: InterceptorContext) -> InterceptorResult[Request]:
        # Build a DRY request payload for logging and delegate to unified helper
        payload = request_payload(context.request, include_body=True)
        self.logger.log_request(payload)
        return InterceptorResult.continue_()


class LoggingResponseInterceptor(ResponseInterceptor):
    def __init__(self, logger: Optional[RequestResponseLogger] = None):
        self.logger = logger or RequestResponseLogger()

    def intercept(self, context: InterceptorContext) -> InterceptorResult[Response]:
        elapsed = context.response.elapsed if context.response else 0
        payload = response_payload(context.request, context.response, elapsed, include_body=True)
        self.logger.log_response(payload)
        return InterceptorResult.continue_()


class LoggingErrorInterceptor(ErrorInterceptor):
    def __init__(self, logger: Optional[RequestResponseLogger] = None):
        self.logger = logger or RequestResponseLogger()

    def intercept(self, context: InterceptorContext) -> InterceptorResult[Exception]:
        payload = error_payload(context.request, context.error)
        self.logger.log_error(payload)
        return InterceptorResult.continue_()
