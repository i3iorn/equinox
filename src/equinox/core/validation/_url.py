"""URL string and structural validation — delegated to urlps.

Structural checks (scheme, host, dangerous schemes, private-IP/metadata
hosts, confusable/punycode hosts) are delegated to urlps' SecurityPolicy.
Local hand-rolled logic is limited to what urlps cannot know about Equinox:
``{{placeholder}}`` tolerance at import time and the
``EQUINOX_SSRF_ALLOW_ON_DNS_FAILURE`` compatibility flag for offline/CI
environments.
"""

from __future__ import annotations

import dataclasses
import logging
import re
from importlib.metadata import PackageNotFoundError, version as _pkg_version

import urlps
from urlps import SecurityPolicy
from urlps.exceptions import URLpError

from equinox.core import urls
from equinox.core.config.flags import is_ssrf_allow_on_dns_failure_enabled
from equinox.core.exceptions import ValidationError

from ._base import _Guards, _Limits

__all__ = ["_UrlValidator"]

_VALID_SCHEMES: frozenset[str] = frozenset({"http", "https"})

_PLACEHOLDER_RE = re.compile(r"\{\{[^}]*\}\}")

_logger = logging.getLogger(__name__)


def _ensure_urlps_compatible() -> None:
    """Fail fast with an actionable message when the installed urlps is stale.

    Equinox requires ``urlps>=2.1.0`` (declared in ``pyproject.toml``), but a
    stale editable install or bundled environment can still carry an older
    package. In that case ``SecurityPolicy(**{...})`` would fail deep inside
    :func:`dataclasses.replace` with a confusing ``TypeError`` about an
    unexpected keyword — this check surfaces the real problem instead.
    """
    if not hasattr(SecurityPolicy, "flag_injection_payloads"):
        try:
            installed = _pkg_version("urlps")
        except PackageNotFoundError:
            installed = "unknown"
        raise ImportError(
            f"Equinox requires urlps>=2.1.0 but installed urlps is {installed}. "
            "Upgrade with: pip install --upgrade 'urlps>=2.1.0'",
        )


_ensure_urlps_compatible()


def _strip_placeholders(url: str) -> str:
    """Replace ``{{PLACEHOLDER}}`` tokens with a parseable stand-in.

    Equinox permits unresolved ``{{placeholders}}`` in host/query positions
    during import/construction; urlps' strict host grammar would otherwise
    reject them outright. The stand-in keeps the URL shape intact for
    structural checks; send-time :meth:`validate_resolved` expands real
    variables first so only leftover placeholders get this treatment.
    """
    return _PLACEHOLDER_RE.sub("<PLACEHOLDER>", url)


def _injection_policy() -> urlps.SecurityPolicy:
    """urlps policy variant that flags injection-shaped payloads.

    Mirrors the ``local`` preset (no DNS, no network) with Equinox's
    opt-in for ``SecurityFindings`` on script/event-handler payloads
    (urlps issue #78). Findings are warnings upstream; Equinox promotes
    them to hard rejections at validation time.
    """
    return dataclasses.replace(
        urlps.SecurityPolicy.local(),
        flag_injection_payloads=True,
    )


_INJECTION_FINDING_CODES: frozenset[str] = frozenset(
    {"script_payload_in_path", "inline_event_handler"},
)


def _check_injection_payloads(url: str) -> None:
    """Reject XSS-shaped payloads in path/query.

    Two complementary layers:

    1. urlps ``security_findings`` with ``flag_injection_payloads`` —
       matches on *decoded* path and query values, so percent-encoded
       variants like ``%3Cscript%3E`` are caught (urlps issue #78).
       Findings are warnings upstream; Equinox promotes them to hard
       rejections. Payloads in queries are not covered upstream, so:
    2. the legacy ``_Guards.check_xss_url`` raw-string regex scan stays
       as the complement (script tags in query strings, ``javascript:``
       URIs) and also serves URLs urlps can't parse (``{{templates}}``).
    """
    _Guards.check_xss_url(url, "URL")

    try:
        parsed = urlps.parse_url_local(url, policy=_injection_policy())
    except URLpError:
        return  # Unparseable/template URL — regex layer above still applied

    codes = {finding.code for finding in parsed.security_findings} & _INJECTION_FINDING_CODES
    if codes:
        _logger.warning(
            "URL validation failed: injection payload detected",
            extra={"codes": sorted(codes)},
        )
        raise ValidationError(
            f"URL contains potentially malicious content ({', '.join(sorted(codes))})",
        )


class _UrlValidator:
    """URL validation delegated to urlps (SSRF, parsing, security policy)."""

    VALID_SCHEMES: frozenset[str] = _VALID_SCHEMES

    @classmethod
    def validate(cls, url: str) -> str:
        """String-level checks only; ``{{placeholders}}`` permitted.

        Use this during import/construction before variables are resolved —
        urlps structural checking is deferred to :meth:`validate_resolved`.
        """
        _Guards.require_nonempty_str(url, "URL")
        url = url.strip()
        if not url:
            _logger.warning("URL validation failed: empty after strip")
            raise ValidationError("URL cannot be empty")

        if len(url) > _Limits.MAX_URL_LENGTH:
            _logger.warning(
                "URL validation failed: exceeds max length",
                extra={"length": len(url), "max_length": _Limits.MAX_URL_LENGTH},
            )
            raise ValidationError(f"URL exceeds maximum length of {_Limits.MAX_URL_LENGTH}")

        _check_injection_payloads(url)

        _logger.debug("URL validation passed", extra={"url_length": len(url)})
        return url

    @classmethod
    def validate_resolved(
        cls,
        url: str,
        *,
        tolerate_dns_failure: bool = False,
    ) -> str:
        """Full structural validation after all ``{{placeholders}}`` are expanded.

        Call this at send-time when the URL is fully resolved. Checks:

        1. urlps ``local`` parse — structural legality (raises on malformed
           URLs, e.g. ``""`` or ``http://`` with no host).
        2. Scheme allow-list (``http``/``https``) and hostname presence.
        3. urlps security policy:

           - ``tolerate_dns_failure=False`` (and no
             ``EQUINOX_SSRF_ALLOW_ON_DNS_FAILURE`` flag): full
             ``resolve_and_validate`` — DNS resolution, SSRF risk, cloud
             metadata endpoints, private/loopback/link-local literals.
           - otherwise: ``SecurityPolicy.strict()`` parse without DNS —
             still blocks private/metadata literal hosts, dangerous
             schemes, confusable hosts; only the DNS-dependent step is
             relaxed so offline/CI environments work.

        Literal-IP and metadata-host protection is never relaxed.
        """
        url = cls.validate(url)

        expanded = urls.expand_placeholders(url, None)
        candidate = _strip_placeholders(expanded)

        try:
            # Step 1: structural parse. parse_url_local always raises on
            # malformed/incomplete input (unlike urlparse's best effort).
            parsed = urlps.parse_url_local(candidate)
        except URLpError as exc:
            _logger.warning(
                "URL parsing failed via urlps",
                extra={"error": str(exc), "url_length": len(candidate)},
            )
            raise ValidationError(f"Invalid URL format: {exc}") from exc

        scheme = parsed.scheme or ""
        netloc = parsed.netloc or ""
        hostname = parsed.host or ""

        # Step 2: scheme + host allow-listing.
        if scheme not in cls.VALID_SCHEMES:
            _logger.warning(
                "URL validation failed: invalid scheme",
                extra={"scheme": scheme, "allowed_schemes": sorted(cls.VALID_SCHEMES)},
            )
            raise ValidationError(
                f"Invalid URL scheme '{scheme}'. Allowed: {', '.join(sorted(cls.VALID_SCHEMES))}",
            )

        if not hostname:
            _logger.warning("URL validation failed: missing hostname")
            raise ValidationError("URL must contain a hostname")

        # Step 3: security policy (SSRF / private networks / metadata).
        _enforce_security_policy(candidate, tolerate_dns_failure=tolerate_dns_failure)

        _logger.debug(
            "URL validation passed",
            extra={"scheme": scheme, "host": hostname, "netloc": netloc},
        )
        return url


def _enforce_security_policy(candidate: str, *, tolerate_dns_failure: bool) -> None:
    """Run urlps SSRF/host checks, tolerating DNS failure when requested.

    Tolerance (``tolerate_dns_failure`` or the
    ``EQUINOX_SSRF_ALLOW_ON_DNS_FAILURE`` flag) relaxes only the
    DNS-resolution step by parsing under ``SecurityPolicy.strict()`` —
    which still blocks literal private IPs and cloud metadata hosts —
    so offline test environments stay functional without losing
    internal-host protection for anything expressible without DNS.
    """
    tolerate = tolerate_dns_failure or is_ssrf_allow_on_dns_failure_enabled()

    try:
        if tolerate:
            # No-DNS enforcement: parse with the strict policy, which
            # rejects SSRF-risk hosts without needing resolution.
            urlps.parse_url(candidate, policy=SecurityPolicy.strict())
        else:
            # Full enforcement: resolve DNS and validate every address.
            urlps.resolve_and_validate(candidate, dns_timeout=2.0)
    except URLpError as exc:
        _logger.warning(
            "URL blocked by urlps security policy",
            extra={"error": str(exc), "candidate": candidate},
        )
        raise ValidationError(f"URL blocked by security policy: {exc}") from exc
