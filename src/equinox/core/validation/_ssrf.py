"""SSRF / private-network protection.

Combines a lazily-initialised DNS thread-pool (``_DnsPool``) with the
actual guard logic (``_SsrfGuard``).  The pool is an implementation detail
of the guard and is not part of the public API of this package.
"""
from __future__ import annotations

import concurrent.futures
import ipaddress
import logging
import socket
import threading
from typing import Optional

from equinox.core.exceptions import ValidationError

__all__ = ["_SsrfGuard"]

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# _DnsPool — private to this module
# ---------------------------------------------------------------------------

class _DnsPool:
    """Lazily-initialised singleton thread-pool for DNS resolution.

    A single worker serialises lookups while still allowing timeout
    enforcement via ``Future.result(timeout=…)``.
    Thread-safe via double-checked locking.
    """

    _instance: Optional[concurrent.futures.ThreadPoolExecutor] = None
    _lock: threading.Lock = threading.Lock()

    @classmethod
    def get(cls) -> concurrent.futures.ThreadPoolExecutor:
        """Return the shared executor, creating it on first call."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = concurrent.futures.ThreadPoolExecutor(
                        max_workers=1,
                        thread_name_prefix="equinox-dns",
                    )
        return cls._instance


# ---------------------------------------------------------------------------
# _SsrfGuard
# ---------------------------------------------------------------------------

class _SsrfGuard:
    """Blocks requests to private/internal IPs and cloud metadata endpoints.

    This is a best-effort check at URL construction time and does **not**
    prevent DNS-rebinding attacks (that requires runtime enforcement).
    """

    _METADATA_HOSTS: frozenset[str] = frozenset({
        "169.254.169.254",           # AWS / GCP / Azure IMDS
        "metadata.google.internal",
        "metadata.goog",
    })

    _DNS_TIMEOUT: float = 2.0   # seconds

    @classmethod
    def check(cls, hostname: str) -> None:
        """Raise ``ValidationError`` if *hostname* targets a private network.

        Check order:
        1. Known cloud-metadata hostnames (exact match).
        2. Literal IP addresses (private, loopback, link-local).
        3. DNS resolution with a tight timeout for hostnames.
        """
        normalized = hostname.lower().rstrip(".")

        cls._check_metadata_host(normalized)

        try:
            addr = ipaddress.ip_address(normalized)
            # Literal IP — check address range; no DNS resolution needed.
            if addr.is_private or addr.is_loopback or addr.is_link_local:
                raise ValidationError(
                    f"Requests to private/internal IP '{hostname}' are blocked "
                    "(SSRF protection)"
                )
            return
        except ValueError:
            pass   # Not a literal IP — fall through to DNS resolution.

        cls._resolve_and_check(normalized, hostname)

    # -- private helpers -----------------------------------------------------

    @classmethod
    def _check_metadata_host(cls, normalized: str) -> None:
        if normalized in cls._METADATA_HOSTS:
            _logger.warning(
                "SSRF protection: blocked metadata endpoint request",
                extra={"hostname": normalized},
            )
            raise ValidationError(
                f"Requests to metadata endpoint '{normalized}' are blocked "
                "(SSRF protection)"
            )

    @classmethod
    def _resolve_and_check(cls, normalized: str, original: str) -> None:
        """DNS-resolve *normalized* and reject any private addresses found."""
        future: Optional[concurrent.futures.Future[list]] = None
        try:
            future = _DnsPool.get().submit(
                socket.getaddrinfo,
                normalized, None, socket.AF_UNSPEC, socket.SOCK_STREAM,
            )
            for _family, _type, _proto, _canon, sockaddr in future.result(
                timeout=cls._DNS_TIMEOUT
            ):
                addr = ipaddress.ip_address(sockaddr[0])
                if addr.is_private or addr.is_loopback or addr.is_link_local:
                    raise ValidationError(
                        f"Hostname '{original}' resolves to private IP "
                        f"{sockaddr[0]} (SSRF protection)"
                    )
        except (socket.gaierror, OSError):
            pass   # DNS failure — allow; will fail at connect time.
        except concurrent.futures.TimeoutError:
            if future is not None:
                future.cancel()
            # DNS timed out — allow; will fail at connect time.

