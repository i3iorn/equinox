"""Parse a cURL command string into a :class:`~equinox.core.request.Request`."""

import logging
import re
import shlex
from typing import Optional
from equinox.core import urls
from equinox.core.security import redact_url

logger = logging.getLogger(__name__)


# Flags that consume the next token as their value.  Defined at module level
# so the set is not recreated on every iteration of the parse loop.
_VALUE_FLAGS = frozenset({
    "-o", "--output",
    "-A", "--user-agent",
    "-e", "--referer",
    "-m", "--max-time", "--connect-timeout",
    "-c", "--cookie-jar",
    "-b", "--cookie",
    "--proxy", "-x",
    "--cacert", "--cert", "--key",
    "--max-redirs",
})


def parse_curl(curl_cmd: str) -> dict:
    """Parse a cURL command string and return a dict suitable for building a Request.

    Supports:
    - ``-X / --request`` (method)
    - ``-H / --header`` (headers)
    - ``-d / --data / --data-raw / --data-binary`` (body)
    - ``-u / --user`` (basic auth → Authorization header)
    - ``--json`` (body + Content-Type: application/json)
    - ``-G / --get`` (force GET)
    - ``--insecure / -k`` (disable SSL verification)
    - URL (positional)

    Returns a dict with keys: ``method``, ``url``, ``headers``, ``body``, ``verify_ssl``.
    """
    # Normalise multi-line curl commands (trailing backslash or caret continuation)
    normalised = curl_cmd.strip()
    logger.debug("parse_curl: input length=%d", len(curl_cmd))
    normalised = re.sub(r'\\\s*\n\s*', ' ', normalised)   # Unix continuation
    normalised = re.sub(r'\^\s*\n\s*', ' ', normalised)   # Windows continuation

    try:
        tokens = shlex.split(normalised)
    except ValueError:
        # Fallback: naive whitespace split
        tokens = normalised.split()

    # Strip leading 'curl' command
    if tokens and tokens[0].lower() == "curl":
        tokens = tokens[1:]

    method: Optional[str] = None
    url: Optional[str] = None
    headers: dict = {}
    body: Optional[str] = None
    verify_ssl: bool = True
    force_get: bool = False

    i = 0
    while i < len(tokens):
        tok = tokens[i]

        if tok in ("-X", "--request"):
            i += 1
            if i < len(tokens):
                method = tokens[i].upper()

        elif tok in ("-H", "--header"):
            i += 1
            if i < len(tokens):
                raw = tokens[i]
                if ":" in raw:
                    key, _, value = raw.partition(":")
                    headers[key.strip()] = value.strip()

        elif tok in ("-d", "--data", "--data-raw", "--data-binary", "--data-ascii"):
            i += 1
            if i < len(tokens):
                body = tokens[i]

        elif tok == "--json":
            i += 1
            if i < len(tokens):
                body = tokens[i]
                headers.setdefault("Content-Type", "application/json")
                headers.setdefault("Accept", "application/json")

        elif tok in ("-u", "--user"):
            i += 1
            if i < len(tokens):
                import base64
                encoded = base64.b64encode(tokens[i].encode()).decode()
                headers["Authorization"] = f"Basic {encoded}"

        elif tok in ("-k", "--insecure"):
            verify_ssl = False

        elif tok in ("-G", "--get"):
            force_get = True

        elif tok in ("-L", "--location"):
            pass  # follow_redirects is True by default

        elif tok.startswith("-"):
            # Unknown flag — skip; if it takes an argument, skip that too.
            if tok in _VALUE_FLAGS and i + 1 < len(tokens):
                i += 1  # skip value

        else:
            # Positional — must be the URL
            if url is None:
                url = tok

        i += 1

    if url is None:
        raise ValueError("No URL found in cURL command")

    if force_get:
        method = "GET"
    if method is None:
        method = "GET" if body is None else "POST"

    # Pass through central placeholder expansion (no-op when no variables provided)
    url = urls.expand_placeholders(url, None)

    logger.debug(
        "parse_curl result: method=%s url=%r headers=%d has_body=%s verify_ssl=%s",
        method,
        redact_url(url),
        len(headers),
        bool(body),
        verify_ssl,
    )

    return {
        "method": method,
        "url": url,
        "headers": headers,
        "body": body,
        "verify_ssl": verify_ssl,
    }
