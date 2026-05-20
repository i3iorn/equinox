"""Base plugin classes"""

import logging
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from dataclasses import dataclass

from equinox.core.request import Request, Response
from equinox.security import redact_body, redact_url

logger = logging.getLogger(__name__)


@dataclass
class PluginContext:
    """Host context for trusted in-process plugins."""

    storage: Any  # Database storage
    http_client: Any  # HTTP client
    config: Dict[str, Any]  # Plugin configuration


class Plugin(ABC):
    """Base class for trusted local plugin extensions."""

    def __init__(self, context: PluginContext):
        """
        Initialize plugin

        Args:
            context: Plugin context with storage, client, and config
        """
        self.context = context
        self.enabled = True

    @property
    @abstractmethod
    def name(self) -> str:
        """Plugin name"""
        pass

    @property
    @abstractmethod
    def version(self) -> str:
        """Plugin version"""
        pass

    @property
    def description(self) -> str:
        """Plugin description"""
        return ""

    def activate(self) -> None:
        """Called when plugin is activated"""
        logger.info("Plugin activated: %s v%s", self.name, self.version)

    def deactivate(self) -> None:
        """Called when plugin is deactivated"""
        logger.info("Plugin deactivated: %s", self.name)

    def on_request(self, request: Request) -> Optional[Request]:
        """
        Called before request is sent. Can modify request.

        Args:
            request: Request object

        Returns:
            Modified request or None to use original
        """
        return None

    def on_response(self, request: Request, response: Response) -> Optional[Response]:
        """
        Called after response is received. Can modify response.

        Args:
            request: Request object
            response: Response object

        Returns:
            Modified response or None to use original
        """
        return None

    def on_error(self, request: Request, error: Exception) -> None:
        """
        Called when request fails

        Args:
            request: Request object
            error: Exception that occurred
        """
        safe_url = redact_url(request.url) if request and request.url else ""
        safe_error = redact_body(str(error), max_length=200)
        logger.debug("Plugin %s on_error: %s %s → %s", self.name, request.method, safe_url, safe_error)


class AuthPlugin(Plugin):
    """Base class for authentication plugins"""

    @abstractmethod
    def apply_auth(self, request: Request, headers: Dict[str, str]) -> None:
        """
        Apply authentication to request

        Args:
            request: Request object
            headers: Headers dictionary to modify
        """
        pass


class TransformPlugin(Plugin):
    """Base class for request/response transformation plugins"""

    def transform_request(self, request: Request) -> Request:
        """Transform outgoing request"""
        return request

    def transform_response(self, response: Response) -> Response:
        """Transform incoming response"""
        return response
