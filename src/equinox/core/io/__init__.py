"""I/O and parsing utilities.

This package contains utilities for parsing cURL commands, environment files, and
handling multipart form data.
"""

from equinox.core.io.curl_parser import parse_curl
from equinox.core.io.dotenv import parse_dotenv

__all__ = [
    "parse_curl",
    "parse_dotenv",
]
