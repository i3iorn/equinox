"""Core serialization / size constants shared across the codebase."""

# Maximum sizes used when serializing history rows or logging.
MAX_BODY_SIZE = 10 * 1024 * 1024  # 10 MB
MAX_HEADERS_SIZE = 100 * 1024  # 100 KB
MAX_URL_LENGTH = 2048
MAX_ERROR_MESSAGE_LENGTH = 10_000
