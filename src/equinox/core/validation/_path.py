"""File-path validation — prevents directory traversal attacks."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote_plus

from equinox.core.exceptions import ValidationError

from ._base import _Guards, _Patterns

__all__ = ["_PathValidator"]


class _PathValidator:
    """File-path validation — prevents directory traversal attacks."""

    @classmethod
    def validate(cls, path: str, base_dir: Path | None = None) -> Path:
        _Guards.require_nonempty_str(path, "Path")

        # Check both the raw path and URL-decoded form (e.g. "..%2F" → "../").
        for candidate in (path, unquote_plus(path)):
            for rx in _Patterns.PATH_TRAVERSAL:
                if rx.search(candidate):
                    raise ValidationError(f"Path contains traversal pattern: {path}")

        try:
            resolved = Path(path).resolve()
        except Exception as exc:
            raise ValidationError(f"Invalid path: {exc}")

        if base_dir is not None:
            try:
                resolved.relative_to(base_dir.resolve())
            except ValueError:
                raise ValidationError(f"Path outside allowed directory: {path}")

        return resolved
