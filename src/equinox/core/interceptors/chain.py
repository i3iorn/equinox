import logging

from equinox.core.interceptors._base import (
    ErrorInterceptor,
    InterceptorAction,
    InterceptorContext,
    RequestInterceptor,
    ResponseInterceptor,
)
from equinox.core.request import Request, Response
from equinox.security import redact_url

logger = logging.getLogger(__name__)


class InterceptorChain:
    def __init__(self) -> None:
        self.request_interceptors: list[RequestInterceptor] = []
        self.response_interceptors: list[ResponseInterceptor] = []
        self.error_interceptors: list[ErrorInterceptor] = []

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
        logger.debug(
            "Processing request through %d interceptor(s)",
            len(self.request_interceptors),
            extra={"method": request.method, "url": redact_url(request.url)},
        )

        for interceptor in self.request_interceptors:
            if context.request is None or not interceptor.can_intercept(context.request):
                continue

            result = interceptor.intercept(context)
            logger.debug(
                "Request interceptor %s returned %s",
                type(interceptor).__name__,
                result.action.value,
            )

            if result.action == InterceptorAction.REPLACE:
                if isinstance(result.value, Request):
                    context.replace_request(result.value)

            elif result.action == InterceptorAction.STOP:
                logger.debug("Request interceptor chain stopped by %s", type(interceptor).__name__)
                break

        if context.request is None:
            raise RuntimeError("Request interceptor chain produced an empty request")
        return context.request

    # -------- Response --------

    def process_response(self, request: Request, response: Response) -> Response:
        context = InterceptorContext(request=request, response=response)
        logger.debug(
            "Processing response through %d interceptor(s)",
            len(self.response_interceptors),
            extra={"status_code": response.status_code},
        )

        for interceptor in self.response_interceptors:
            if context.response is None or not interceptor.can_intercept(context.response):
                continue

            result = interceptor.intercept(context)
            logger.debug(
                "Response interceptor %s returned %s",
                type(interceptor).__name__,
                result.action.value,
            )

            if result.action == InterceptorAction.REPLACE:
                if isinstance(result.value, Response):
                    context.replace_response(result.value)

            elif result.action == InterceptorAction.STOP:
                logger.debug("Response interceptor chain stopped by %s", type(interceptor).__name__)
                break

        if context.response is None:
            raise RuntimeError("Response interceptor chain produced an empty response")
        return context.response

    # -------- Error --------

    def process_error(self, request: Request, error: Exception) -> Exception | None:
        context = InterceptorContext(request=request, error=error)
        logger.debug(
            "Processing error through %d interceptor(s): %s",
            len(self.error_interceptors),
            type(error).__name__,
        )

        for interceptor in self.error_interceptors:
            if context.error is None or not interceptor.can_intercept(context.error, context.request):
                continue

            result = interceptor.intercept(context)
            logger.debug(
                "Error interceptor %s returned %s", type(interceptor).__name__, result.action.value
            )

            if result.action == InterceptorAction.SUPPRESS:
                logger.debug("Error suppressed by %s", type(interceptor).__name__)
                return None

            if result.action == InterceptorAction.REPLACE:
                if isinstance(result.value, Exception):
                    context.replace_error(result.value)

            elif result.action == InterceptorAction.STOP:
                logger.debug("Error interceptor chain stopped by %s", type(interceptor).__name__)
                break

        return context.error
