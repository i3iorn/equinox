"""Parse a cURL command string into a :class:`~equinox.core.request.Request`."""

import base64
import logging
import re
import shlex
from dataclasses import dataclass, field
from typing import Any

from equinox.core import urls
from equinox.security import redact_url

logger = logging.getLogger(__name__)


# Flags that consume the next token as their value.  Defined at module level
# so the set is not recreated on every iteration of the parse loop.
_VALUE_FLAGS = frozenset(
    {
        "-o",
        "--output",
        "-A",
        "--user-agent",
        "-e",
        "--referer",
        "-m",
        "--max-time",
        "--connect-timeout",
        "-c",
        "--cookie-jar",
        "-b",
        "--cookie",
        "--proxy",
        "-x",
        "--cacert",
        "--cert",
        "--key",
        "--max-redirs",
    }
)

_METHOD_FLAGS = frozenset({"-X", "--request"})
_HEADER_FLAGS = frozenset({"-H", "--header"})
_DATA_FLAGS = frozenset({"-d", "--data", "--data-raw", "--data-binary", "--data-ascii"})
_BASIC_AUTH_FLAGS = frozenset({"-u", "--user"})


@dataclass
class _ParserState:
    method: str | None = None
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    body: str | None = None
    verify_ssl: bool = True
    force_get: bool = False


def _normalize_command(curl_cmd: str) -> str:
    """Normalize multiline cURL command continuations to single line."""
    normalised = curl_cmd.strip()
    normalised = re.sub(r"\\\s*\n\s*", " ", normalised)  # Unix continuation
    normalised = re.sub(r"\^\s*\n\s*", " ", normalised)  # Windows continuation
    return normalised


def _tokenize_command(normalised: str) -> list[str]:
    """Tokenize normalized command, falling back to whitespace split."""
    try:
        tokens = shlex.split(normalised)
    except ValueError:
        tokens = normalised.split()
    if tokens and tokens[0].lower() == "curl":
        return tokens[1:]
    return tokens


def _consume_value(tokens: list[str], index: int) -> tuple[str | None, int]:
    """Return (value, updated_index) for flags that consume next token."""
    next_index = index + 1
    if next_index < len(tokens):
        return tokens[next_index], next_index
    return None, index


def _parse_header(raw_header: str, headers: dict[str, str]) -> None:
    """Parse and insert a single header token if in key:value form."""
    if ":" not in raw_header:
        return
    key, _, value = raw_header.partition(":")
    headers[key.strip()] = value.strip()


def _consume_method_header_or_data(
    tok: str, tokens: list[str], index: int, state: _ParserState
) -> int | None:
    """Consume value flags for method/header/body-like arguments."""
    if tok in _METHOD_FLAGS:
        value, index = _consume_value(tokens, index)
        if value is not None:
            state.method = value.upper()
        return index

    if tok in _HEADER_FLAGS:
        value, index = _consume_value(tokens, index)
        if value is not None:
            _parse_header(value, state.headers)
        return index

    if tok in _DATA_FLAGS:
        value, index = _consume_value(tokens, index)
        if value is not None:
            state.body = value
        return index

    return None


def _consume_json_or_auth(
    tok: str, tokens: list[str], index: int, state: _ParserState
) -> int | None:
    """Consume value flags for JSON shorthand and basic auth."""

    if tok == "--json":
        value, index = _consume_value(tokens, index)
        if value is not None:
            state.body = value
            state.headers.setdefault("Content-Type", "application/json")
            state.headers.setdefault("Accept", "application/json")
        return index

    if tok in _BASIC_AUTH_FLAGS:
        value, index = _consume_value(tokens, index)
        if value is not None:
            encoded = base64.b64encode(value.encode()).decode()
            state.headers["Authorization"] = f"Basic {encoded}"
        return index

    return None


def _apply_token(tok: str, tokens: list[str], index: int, state: _ParserState) -> int:
    """Apply one token to parser state and return updated index."""
    consumed_index = _consume_method_header_or_data(tok, tokens, index, state)
    if consumed_index is not None:
        return consumed_index

    consumed_index = _consume_json_or_auth(tok, tokens, index, state)
    if consumed_index is not None:
        return consumed_index

    if tok in ("-k", "--insecure"):
        state.verify_ssl = False
        return index

    if tok in ("-G", "--get"):
        state.force_get = True
        return index

    if tok.startswith("-"):
        if tok in _VALUE_FLAGS and index + 1 < len(tokens):
            return index + 1
        return index

    if state.url is None:
        state.url = tok
    return index


def _resolve_method(state: _ParserState) -> str:
    """Determine effective method after flag parsing."""
    if state.force_get:
        return "GET"
    if state.method is not None:
        return state.method
    return "GET" if state.body is None else "POST"


def _initial_state() -> _ParserState:
    """Return a fresh parser state container."""
    return _ParserState()


def _finalize_parse_result(state: _ParserState) -> dict[str, Any]:
    """Build final parse result after token processing."""
    url: str | None = state.url
    if url is None:
        raise ValueError("No URL found in cURL command")

    method = _resolve_method(state)
    url = urls.expand_placeholders(url, None)

    logger.debug(
        "parse_curl result: method=%s url=%r headers=%d has_body=%s verify_ssl=%s",
        method,
        redact_url(url),
        len(state.headers),
        bool(state.body),
        state.verify_ssl,
    )

    return {
        "method": method,
        "url": url,
        "headers": state.headers,
        "body": state.body,
        "verify_ssl": state.verify_ssl,
    }


def parse_curl(curl_cmd: str) -> dict[str, Any]:
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
    logger.debug("parse_curl: input length=%d", len(curl_cmd))
    normalised = _normalize_command(curl_cmd)
    tokens = _tokenize_command(normalised)

    state = _initial_state()

    i = 0
    while i < len(tokens):
        i = _apply_token(tokens[i], tokens, i, state)
        i += 1

    return _finalize_parse_result(state)
