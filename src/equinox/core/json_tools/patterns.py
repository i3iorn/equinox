from __future__ import annotations

import re

# Number: -?(0|[1-9]\d*)(.\d+)?([eE][+-]?\d+)?
NUMBER_RE = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?")

# ISO-8601 timestamp: YYYY-MM-DDTHH:MM:SS[.fractional][timezone]
TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?")

# Unicode escape sequence validator (\uXXXX)
UNICODE_HEX_RE = re.compile(r"[0-9a-fA-F]{4}")

# JSON5 literal keywords (lowercase name → uppercase token type)
JSON5_LITERALS: dict[str, str] = {
    "true": "TRUE",
    "false": "FALSE",
    "null": "NULL",
}

# Valid escape sequences in JSON strings
JSON_ESCAPE_CHARS: frozenset[str] = frozenset(r'"\\/bfnrt')

# Control character threshold — characters below this are errors in JSON strings
CONTROL_CHAR_THRESHOLD: int = 0x20
