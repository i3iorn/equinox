"""Proxy reachability checker extracted from HTTPClient.

Provides a cross-platform check that attempts a non-blocking TCP connect to
the proxy host:port and raises :class:`equinox.core.exceptions.RequestError`
when the proxy actively refuses connections.
"""

import errno
import logging
import select as _select
import socket

from equinox.core import urls
from equinox.core.exceptions import RequestError

logger = logging.getLogger(__name__)

_REFUSED = {errno.ECONNREFUSED, getattr(errno, "WSAECONNREFUSED", 10061)}


def _parse_proxy_target(proxy_url: str) -> tuple[str, int, dict[str, str]]:
    parsed = urls.url_metadata(proxy_url)
    host = parsed.get("hostname") or ""
    port = parsed.get("port")
    if not isinstance(port, int):
        logger.debug("Proxy check skipped: invalid port %s. Using default", port)
        port = 8080
    return host, port, parsed


def _raise_refused(proxy_url: str, host: str, port: int, err: int, errno_name: str = "") -> None:
    details = {
        "proxy": proxy_url,
        "host": host,
        "port": port,
        "errno": err,
        "error_type": "connection_refused",
    }
    if errno_name:
        details["errno_name"] = errno_name
    raise RequestError(
        f"Failed to connect to proxy ({proxy_url}). The proxy server is not running or refusing connections.",
        details=details,
    )


def _check_select_result(
    sock: socket.socket,
    host: str,
    port: int,
    timeout: float,
    proxy_url: str,
) -> None:
    _, writable, exceptional = _select.select([], [sock], [sock], timeout)
    logger.debug(
        "Proxy pre-flight select() after %.1fs: writable=%s exceptional=%s",
        timeout,
        bool(writable),
        bool(exceptional),
    )
    if not (exceptional or writable):
        return

    err = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
    logger.debug("Proxy pre-flight SO_ERROR for %s:%s = %d", host, port, err)
    if err in _REFUSED:
        _raise_refused(proxy_url, host, port, err)


def _connect_non_blocking(
    sock: socket.socket,
    host: str,
    port: int,
    timeout: float,
    proxy_url: str,
) -> None:
    try:
        logger.debug("Attempting non-blocking connect to %s:%s", host, port)
        sock.connect((host, port))
        logger.debug("Proxy pre-flight: %s:%s connected immediately", host, port)
    except BlockingIOError:
        logger.debug("Proxy pre-flight: BlockingIOError on connect (expected)")
        _check_select_result(sock, host, port, timeout, proxy_url)


def check_proxy_reachable(proxy_url: str) -> None:
    """Raise RequestError if the proxy is unreachable/refuses connections."""
    host, port, parsed = _parse_proxy_target(proxy_url)
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
    logger.debug(
        "Pre-flight proxy reachability check: %s:%s (timeout=%.1fs, loopback=%s)",
        host,
        port,
        connect_timeout,
        is_loopback,
    )

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setblocking(False)
    try:
        _connect_non_blocking(sock, host, port, connect_timeout, proxy_url)
    except OSError as os_err:
        err_no = os_err.errno
        errno_name = errno.errorcode.get(err_no, "unknown") if err_no is not None else "unknown"
        logger.debug(
            "Proxy pre-flight OSError for %s:%s (errno %d = %s): %s",
            host,
            port,
            err_no or -1,
            errno_name,
            os_err,
        )
        if err_no is not None and err_no in _REFUSED:
            _raise_refused(proxy_url, host, port, err_no, errno_name)
        logger.debug(
            "Proxy pre-flight socket error for %s:%s (errno %d = %s, will defer to httpx): %s",
            host,
            port,
            err_no or -1,
            errno_name,
            os_err,
        )
    finally:
        try:
            sock.close()
        except Exception:
            pass
        logger.debug("Proxy pre-flight: socket closed for %s:%s", host, port)
