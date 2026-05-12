"""Proxy reachability checker extracted from HTTPClient.

Provides a cross-platform check that attempts a non-blocking TCP connect to
the proxy host:port and raises :class:`equinox.core.exceptions.RequestError`
when the proxy actively refuses connections.
"""

import logging
import errno
import select as _select
import socket
from typing import Optional

from equinox.core.exceptions import RequestError
from equinox.core import urls

logger = logging.getLogger(__name__)


def check_proxy_reachable(proxy_url: str) -> None:
    """Raise RequestError if the proxy is unreachable/refuses connections.

    This function mirrors the previous behaviour in ``HTTPClient._check_proxy_reachable``.
    """
    parsed = urls.url_metadata(proxy_url)
    host = parsed.get("hostname")
    port = int(parsed.get("port") or 8080)
    if not host:
        logger.debug("Proxy check skipped: no hostname in proxy URL")
        return

    logger.debug(
        "Proxy details: scheme=%s hostname=%s port=%d netloc=%s",
        parsed.get("scheme", ""),
        host,
        port,
        parsed.get("netloc", ""),
    )

    is_loopback = host in ("127.0.0.1", "::1", "localhost")
    connect_timeout = 3.5 if is_loopback else 1.5
    _REFUSED = {errno.ECONNREFUSED, getattr(errno, "WSAECONNREFUSED", 10061)}

    logger.debug("Pre-flight proxy reachability check: %s:%s (timeout=%.1fs, loopback=%s)", host, port, connect_timeout, is_loopback)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setblocking(False)
    try:
        logger.debug("Attempting non-blocking connect to %s:%s", host, port)
        sock.connect((host, port))
        logger.debug("Proxy pre-flight: %s:%s connected immediately", host, port)
    except BlockingIOError:
        logger.debug("Proxy pre-flight: BlockingIOError on connect (expected)")
        _, writable, exceptional = _select.select([], [sock], [sock], connect_timeout)
        logger.debug("Proxy pre-flight select() after %.1fs: writable=%s exceptional=%s", connect_timeout, bool(writable), bool(exceptional))
        if exceptional or writable:
            err = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
            logger.debug("Proxy pre-flight SO_ERROR for %s:%s = %d", host, port, err)
            if err in _REFUSED:
                raise RequestError(
                    f"Failed to connect to proxy ({proxy_url}). The proxy server is not running or refusing connections.",
                    details={"proxy": proxy_url, "host": host, "port": port, "errno": err, "error_type": "connection_refused"},
                )
    except OSError as os_err:
        errno_name = errno.errorcode.get(os_err.errno, "unknown")
        logger.debug("Proxy pre-flight OSError for %s:%s (errno %d = %s): %s", host, port, os_err.errno, errno_name, os_err)
        if os_err.errno in _REFUSED:
            raise RequestError(
                f"Failed to connect to proxy ({proxy_url}). The proxy server is not running or refusing connections.",
                details={"proxy": proxy_url, "host": host, "port": port, "errno": os_err.errno, "errno_name": errno_name, "error_type": "connection_refused"},
            )
        logger.debug("Proxy pre-flight socket error for %s:%s (errno %d = %s, will defer to httpx): %s", host, port, os_err.errno, errno_name, os_err)
    finally:
        try:
            sock.close()
        except Exception:
            pass
        logger.debug("Proxy pre-flight: socket closed for %s:%s", host, port)

