"""SSRF / private-network protection.

Combines a lazily-initialised DNS thread-pool (``_DnsPool``) with the
actual guard logic (``_SsrfGuard``).  The pool is an implementation detail
of the guard and is not part of the public API of this package.
"""
from __future__ import annotations

import concurrent.futures
import time
import ipaddress
import logging
import socket
import threading
from collections import OrderedDict
from typing import Optional, Tuple

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
        executor = cls._instance
        if executor is None:
            raise RuntimeError("DNS executor initialization failed")
        return executor


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
    _DNS_CACHE_TTL_SECONDS: float = 60.0
    _DNS_CACHE_MAX_ENTRIES: int = 512
    _dns_cache_lock: threading.Lock = threading.Lock()
    _dns_cache: "OrderedDict[str, Tuple[float, bool]]" = OrderedDict()

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
        cached = cls._get_cached_dns_result(normalized)
        if cached is not None:
            if cached:
                raise ValidationError(
                    f"Hostname '{original}' resolves to private IP (SSRF protection)"
                )
            return

        future: Optional[concurrent.futures.Future[list]] = None
        try:
            has_private = False
            future = _DnsPool.get().submit(
                socket.getaddrinfo,
                normalized, None, socket.AF_UNSPEC, socket.SOCK_STREAM,
            )
            for _family, _type, _proto, _canon, sockaddr in future.result(
                timeout=cls._DNS_TIMEOUT
            ):
                addr = ipaddress.ip_address(sockaddr[0])
                if addr.is_private or addr.is_loopback or addr.is_link_local:
                    has_private = True
                    break

            cls._cache_dns_result(normalized, has_private)
            if has_private:
                raise ValidationError(
                    f"Hostname '{original}' resolves to private IP (SSRF protection)"
                )
        except (socket.gaierror, OSError):
            pass   # DNS failure — allow; will fail at connect time.
        except concurrent.futures.TimeoutError:
            if future is not None:
                future.cancel()
            # DNS timed out — allow; will fail at connect time.

    @classmethod
    def _get_cached_dns_result(cls, hostname: str) -> Optional[bool]:
        """Return cached private-IP result when fresh, otherwise ``None``."""
        now = time.monotonic()
        with cls._dns_cache_lock:
            cached = cls._dns_cache.get(hostname)
            if cached is None:
                return None

            ts, has_private = cached
            if now - ts > cls._DNS_CACHE_TTL_SECONDS:
                cls._dns_cache.pop(hostname, None)
                return None

            cls._dns_cache.move_to_end(hostname)
            return has_private

    @classmethod
    def _cache_dns_result(cls, hostname: str, has_private: bool) -> None:
        """Store DNS classification in a bounded LRU-like cache."""
        with cls._dns_cache_lock:
            cls._dns_cache[hostname] = (time.monotonic(), has_private)
            cls._dns_cache.move_to_end(hostname)
            while len(cls._dns_cache) > cls._DNS_CACHE_MAX_ENTRIES:
                cls._dns_cache.popitem(last=False)

