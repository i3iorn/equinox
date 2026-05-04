from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TypeVar, Generic, Optional, Dict, Any

from equinox.core.request import Request, Response
from equinox.core.time import utc_now

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
