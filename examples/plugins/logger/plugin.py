"""
Logger Plugin for Equinox

Logs all HTTP requests and responses to a file.
"""

import logging
from pathlib import Path

from equinox.core.request import Request, Response
from equinox.plugins import Plugin


class PluginClass(Plugin):
    """Logger plugin implementation"""

    @property
    def name(self) -> str:
        return "logger"

    @property
    def version(self) -> str:
        return "1.0.0"

    @property
    def description(self) -> str:
        return "Logs all HTTP requests and responses"

    def activate(self):
        """Set up logging"""
        log_file = Path.home() / ".equinox" / "requests.log"
        log_file.parent.mkdir(parents=True, exist_ok=True)

        # Configure logging
        logging.basicConfig(
            filename=str(log_file),
            level=logging.INFO,
            format="%(asctime)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        self.logger = logging.getLogger("equinox.logger")
        self.logger.info("Logger plugin activated")

    def deactivate(self):
        """Cleanup"""
        self.logger.info("Logger plugin deactivated")

    def on_request(self, request: Request):
        """Log outgoing request"""
        self.logger.info(
            f"→ {request.method} {request.url} | "
            f"Headers: {len(request.headers)} | "
            f"Body: {len(request.body) if request.body else 0} bytes"
        )
        return None

    def on_response(self, request: Request, response: Response):
        """Log incoming response"""
        self.logger.info(
            f"← {response.status_code} {response.reason} | "
            f"Time: {response.elapsed:.3f}s | "
            f"Size: {response.size} bytes"
        )

        # Log errors
        if response.status_code >= 400:
            self.logger.warning(f"Request failed with status {response.status_code}")

        return None

    def on_error(self, request: Request, error: Exception):
        """Log request errors"""
        self.logger.error(f"✗ {request.method} {request.url} | Error: {str(error)}")
