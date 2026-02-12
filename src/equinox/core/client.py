"""HTTP Client implementation using httpx"""

import httpx
import time
from typing import Optional, Dict, Any
from datetime import datetime

from equinox.core.request import Request, Response
from equinox.core.exceptions import RequestError
from equinox.auth.base import AuthStrategy


class HTTPClient:
    """HTTP Client for making requests"""

    def __init__(
        self,
        timeout: float = 30.0,
        follow_redirects: bool = True,
        verify_ssl: bool = True,
        proxy: Optional[str] = None,
    ):
        """
        Initialize HTTP client

        Args:
            timeout: Request timeout in seconds
            follow_redirects: Whether to follow redirects
            verify_ssl: Whether to verify SSL certificates
            proxy: Proxy URL (e.g., 'http://localhost:8080')
        """
        self.timeout = timeout
        self.follow_redirects = follow_redirects
        self.verify_ssl = verify_ssl
        self.proxy = proxy
        self._client: Optional[httpx.Client] = None

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

    def send(self, request: Request, auth: Optional[AuthStrategy] = None) -> Response:
        """
        Send HTTP request

        Args:
            request: Request object
            auth: Optional auth strategy

        Returns:
            Response object

        Raises:
            RequestError: If request fails
        """
        # Use context manager if client not already initialized
        if self._client is None:
            with self:
                return self._send_internal(request, auth)
        else:
            return self._send_internal(request, auth)

    def _send_internal(self, request: Request, auth: Optional[AuthStrategy] = None) -> Response:
        """Internal method to send request"""
        try:
            # Apply auth if provided
            headers = dict(request.headers)
            if auth:
                auth.apply(request, headers)
            elif request.auth:
                request.auth.apply(request, headers)

            # Start timer
            start_time = time.time()

            # Send request
            response = self._client.request(
                method=request.method,
                url=request.url,
                headers=headers,
                params=request.params,
                content=request.body.encode() if request.body else None,
                timeout=request.timeout or self.timeout,
                follow_redirects=request.follow_redirects,
            )

            # Calculate elapsed time
            elapsed = time.time() - start_time

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
            raise RequestError(f"Request timeout: {e}")
        except httpx.ConnectError as e:
            raise RequestError(f"Connection error: {e}")
        except httpx.HTTPError as e:
            raise RequestError(f"HTTP error: {e}")
        except Exception as e:
            raise RequestError(f"Request failed: {e}")

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
