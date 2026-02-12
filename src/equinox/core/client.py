"""HTTP Client implementation using httpx"""

import httpx
import time
import logging
from typing import Optional, Dict, Any
from datetime import datetime
from threading import Lock
from collections import defaultdict

from equinox.core.request import Request, Response
from equinox.core.exceptions import (
    RequestError, TimeoutError, RateLimitError,
    CertificateError, ValidationError
)
from equinox.core.validation import Validator
from equinox.auth.base import AuthStrategy

# Configure logging
logger = logging.getLogger(__name__)


class HTTPClient:
    """HTTP Client for making requests with security features.

    Features:
    - Input validation
    - Rate limiting
    - Timeout controls
    - SSL/TLS verification
    - Comprehensive error handling
    """

    # Default limits
    MAX_TIMEOUT = 300.0  # 5 minutes
    MIN_TIMEOUT = 0.1    # 100ms
    DEFAULT_TIMEOUT = 30.0
    MAX_REDIRECTS = 10
    MAX_RETRIES = 3

    def __init__(
        self,
        timeout: float = 30.0,
        follow_redirects: bool = True,
        verify_ssl: bool = True,
        proxy: Optional[str] = None,
        max_rate_per_minute: int = 60,
        max_concurrent_requests: int = 10,
    ):
        """Initialize HTTP client.

        Args:
            timeout: Request timeout in seconds (0.1 to 300)
            follow_redirects: Whether to follow redirects
            verify_ssl: Whether to verify SSL certificates
            proxy: Proxy URL (e.g., 'http://localhost:8080')
            max_rate_per_minute: Maximum requests per minute (0 = unlimited)
            max_concurrent_requests: Maximum concurrent requests

        Raises:
            ValidationError: If parameters are invalid
        """
        # Validate timeout
        if not isinstance(timeout, (int, float)) or timeout <= 0:
            raise ValidationError("Timeout must be a positive number")

        if timeout < self.MIN_TIMEOUT:
            logger.warning(f"Timeout {timeout}s is very low, using minimum {self.MIN_TIMEOUT}s")
            timeout = self.MIN_TIMEOUT
        elif timeout > self.MAX_TIMEOUT:
            logger.warning(f"Timeout {timeout}s exceeds maximum, using {self.MAX_TIMEOUT}s")
            timeout = self.MAX_TIMEOUT

        self.timeout = timeout
        self.follow_redirects = follow_redirects
        self.verify_ssl = verify_ssl
        self.proxy = proxy
        self.max_rate_per_minute = max_rate_per_minute
        self.max_concurrent_requests = max_concurrent_requests

        self._client: Optional[httpx.Client] = None

        # Rate limiting
        self._rate_limit_lock = Lock()
        self._request_times: list[float] = []

        # Request tracking
        self._active_requests = 0
        self._request_lock = Lock()

    def __enter__(self):
        """Context manager entry"""
        self._client = httpx.Client(
            timeout=self.timeout,
            follow_redirects=self.follow_redirects,
            verify=self.verify_ssl,
            proxy=self.proxy,
        )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        if self._client:
            self._client.close()
            self._client = None

    def _check_rate_limit(self) -> None:
        """Check if rate limit allows new request.

        Raises:
            RateLimitError: If rate limit exceeded
        """
        if self.max_rate_per_minute <= 0:
            return  # No rate limiting

        with self._rate_limit_lock:
            now = time.time()
            # Remove requests older than 1 minute
            self._request_times = [t for t in self._request_times if now - t < 60]

            if len(self._request_times) >= self.max_rate_per_minute:
                raise RateLimitError(
                    f"Rate limit exceeded: {self.max_rate_per_minute} requests per minute"
                )

            self._request_times.append(now)

    def _check_concurrent_limit(self) -> None:
        """Check if concurrent request limit allows new request.

        Raises:
            RequestError: If too many concurrent requests
        """
        with self._request_lock:
            if self._active_requests >= self.max_concurrent_requests:
                raise RequestError(
                    f"Too many concurrent requests: {self._active_requests}/{self.max_concurrent_requests}"
                )
            self._active_requests += 1

    def _release_concurrent_slot(self) -> None:
        """Release a concurrent request slot."""
        with self._request_lock:
            self._active_requests = max(0, self._active_requests - 1)

    def send(self, request: Request, auth: Optional[AuthStrategy] = None) -> Response:
        """Send HTTP request with validation and security checks.

        Args:
            request: Request object
            auth: Optional auth strategy

        Returns:
            Response object

        Raises:
            ValidationError: If request validation fails
            RateLimitError: If rate limit exceeded
            RequestError: If request fails
            TimeoutError: If request times out
            CertificateError: If SSL verification fails
        """
        # Validate request before sending
        try:
            self._validate_request(request)
        except ValidationError as e:
            logger.error(f"Request validation failed: {e}")
            raise

        # Check rate limit
        self._check_rate_limit()

        # Check concurrent limit
        self._check_concurrent_limit()

        try:
            # Use context manager if client not already initialized
            if self._client is None:
                with self:
                    return self._send_internal(request, auth)
            else:
                return self._send_internal(request, auth)
        finally:
            # Always release concurrent slot
            self._release_concurrent_slot()

    def _validate_request(self, request: Request) -> None:
        """Validate request before sending.

        Args:
            request: Request to validate

        Raises:
            ValidationError: If validation fails
        """
        # Validate URL
        Validator.validate_url(request.url)

        # Validate method
        Validator.validate_method(request.method)

        # Validate headers
        if request.headers:
            Validator.validate_headers(request.headers)

        # Validate query parameters
        if request.params:
            Validator.validate_query_params(request.params)

        # Validate body
        if request.body:
            content_type = request.headers.get('Content-Type')
            Validator.validate_request_body(request.body, content_type)

    def _send_internal(self, request: Request, auth: Optional[AuthStrategy] = None) -> Response:
        """Internal method to send request with comprehensive error handling.

        Args:
            request: Request to send
            auth: Optional authentication strategy

        Returns:
            Response object

        Raises:
            TimeoutError: If request times out
            CertificateError: If SSL verification fails
            RequestError: For other request errors
        """
        try:
            # Apply auth if provided
            headers = dict(request.headers) if request.headers else {}

            if auth:
                try:
                    auth.apply(request, headers)
                except Exception as e:
                    logger.error(f"Authentication failed: {e}")
                    raise RequestError(f"Authentication failed: {e}")
            elif request.auth:
                try:
                    request.auth.apply(request, headers)
                except Exception as e:
                    logger.error(f"Authentication failed: {e}")
                    raise RequestError(f"Authentication failed: {e}")

            # Start timer
            start_time = time.time()

            # Log request (sanitized)
            logger.debug(f"Sending {request.method} request to {Validator.sanitize_for_display(request.url, 100)}")

            # Send request
            response = self._client.request(
                method=request.method,
                url=request.url,
                headers=headers,
                params=request.params,
                content=request.body.encode('utf-8') if request.body else None,
                timeout=request.timeout or self.timeout,
                follow_redirects=request.follow_redirects if request.follow_redirects is not None else self.follow_redirects,
            )

            # Calculate elapsed time
            elapsed = time.time() - start_time

            logger.debug(f"Request completed in {elapsed:.2f}s with status {response.status_code}")

            # Create Response object
            return Response(
                status_code=response.status_code,
                reason=response.reason_phrase,
                headers=dict(response.headers),
                body=response.content,
                elapsed=elapsed,
                request=request,
                timestamp=datetime.now(),
            )

        except httpx.TimeoutException as e:
            logger.error(f"Request timeout after {self.timeout}s: {e}")
            raise TimeoutError(
                f"Request timed out after {self.timeout} seconds",
                details={"url": request.url, "timeout": self.timeout}
            )

        except httpx.ConnectTimeout as e:
            logger.error(f"Connection timeout: {e}")
            raise TimeoutError(
                "Connection timed out",
                details={"url": request.url}
            )

        except httpx.ReadTimeout as e:
            logger.error(f"Read timeout: {e}")
            raise TimeoutError(
                "Server response timed out",
                details={"url": request.url}
            )

        except httpx.SSLError as e:
            logger.error(f"SSL certificate verification failed: {e}")
            raise CertificateError(
                "SSL certificate verification failed. The server's certificate is invalid or untrusted.",
                details={"url": request.url, "ssl_error": str(e)}
            )

        except httpx.ConnectError as e:
            logger.error(f"Connection error: {e}")
            raise RequestError(
                "Failed to connect to server. Please check the URL and your network connection.",
                details={"url": request.url, "error": str(e)}
            )

        except httpx.TooManyRedirects as e:
            logger.error(f"Too many redirects: {e}")
            raise RequestError(
                f"Too many redirects (max: {self.MAX_REDIRECTS})",
                details={"url": request.url}
            )

        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error status: {e}")
            raise RequestError(
                f"HTTP error: {e.response.status_code}",
                details={"url": request.url, "status": e.response.status_code}
            )

        except httpx.HTTPError as e:
            logger.error(f"HTTP error: {e}")
            raise RequestError(
                "HTTP request failed",
                details={"url": request.url, "error": str(e)}
            )

        except UnicodeEncodeError as e:
            logger.error(f"Encoding error: {e}")
            raise RequestError(
                "Request body contains invalid characters",
                details={"error": str(e)}
            )

        except Exception as e:
            logger.error(f"Unexpected error during request: {type(e).__name__}: {e}")
            raise RequestError(
                "Request failed due to unexpected error",
                details={"error": type(e).__name__}
            )

    def get(self, url: str, **kwargs) -> Response:
        """Convenience method for GET request"""
        request = Request(method="GET", url=url, **kwargs)
        return self.send(request)

    def post(self, url: str, body: Optional[str] = None, **kwargs) -> Response:
        """Convenience method for POST request"""
        request = Request(method="POST", url=url, body=body, **kwargs)
        return self.send(request)

    def put(self, url: str, body: Optional[str] = None, **kwargs) -> Response:
        """Convenience method for PUT request"""
        request = Request(method="PUT", url=url, body=body, **kwargs)
        return self.send(request)

    def patch(self, url: str, body: Optional[str] = None, **kwargs) -> Response:
        """Convenience method for PATCH request"""
        request = Request(method="PATCH", url=url, body=body, **kwargs)
        return self.send(request)

    def delete(self, url: str, **kwargs) -> Response:
        """Convenience method for DELETE request"""
        request = Request(method="DELETE", url=url, **kwargs)
        return self.send(request)

    def head(self, url: str, **kwargs) -> Response:
        """Convenience method for HEAD request"""
        request = Request(method="HEAD", url=url, **kwargs)
        return self.send(request)

    def options(self, url: str, **kwargs) -> Response:
        """Convenience method for OPTIONS request"""
        request = Request(method="OPTIONS", url=url, **kwargs)
        return self.send(request)
