"""Content-related shared helpers."""

_COMPRESSIBLE_TOKENS: tuple[str, ...] = (
    "json",
    "xml",
    "html",
    "text",
    "javascript",
    "css",
    "svg",
)


def is_compressible_content_type(content_type: str) -> bool:
    ct = (content_type or "").lower()
    return any(token in ct for token in _COMPRESSIBLE_TOKENS)


def format_bytes(size: int) -> str:
    if size >= 1_048_576:
        return f"{size / 1_048_576:.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size} B"
