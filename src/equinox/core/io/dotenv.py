"""Shared .env file parser used by both the CLI and GUI."""

import logging

logger = logging.getLogger(__name__)

# Maximum size of a .env file to prevent memory exhaustion (1 MB).
MAX_DOTENV_SIZE = 1 * 1024 * 1024


def parse_dotenv(text: str) -> dict[str, str]:
    """Parse a .env file text and return {key: value}.

    Handles ``KEY=VALUE``, ``export KEY=VALUE``, quoted values,
    comment lines (``#``), and blank lines.

    Raises:
        ValueError: If the text exceeds the maximum allowed size.
    """
    if len(text.encode("utf-8")) > MAX_DOTENV_SIZE:
        raise ValueError(f".env content exceeds maximum size ({MAX_DOTENV_SIZE} bytes)")
    result: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if value.startswith('"') or value.startswith("'"):
            # Handle quoted values, including an inline comment after the
            # closing quote: KEY="value" # note  →  value
            quote = value[0]
            end = 1
            while end < len(value):
                if value[end] == quote and value[end - 1] != "\\":
                    break
                end += 1
            if end < len(value):
                value = value[1:end]
            # Unterminated quote: fall back to the unquoted handling below.
            else:
                comment_idx = value.find(" #")
                if comment_idx >= 0:
                    value = value[:comment_idx].rstrip()
        else:
            # Strip inline comments for unquoted values: KEY=value # comment
            # Only strip if there's a space before the #, so values like
            # "color=#fff" are preserved.
            comment_idx = value.find(" #")
            if comment_idx >= 0:
                value = value[:comment_idx].rstrip()
        if key:
            result[key] = str(value)
    logger.debug("parse_dotenv: loaded %d variable(s)", len(result))
    return dict(result)
