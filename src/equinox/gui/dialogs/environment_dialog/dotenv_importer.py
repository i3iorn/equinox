from __future__ import annotations

from dataclasses import dataclass

from equinox.core.io import parse_dotenv
from equinox.gui.file_ops import safe_read_text_file, validate_selected_path

_MAX_BYTES = 200_000  # 200 KB safety cap


@dataclass(frozen=True)
class DotenvImportResult:
    """Structured result of a dotenv import operation."""

    added: dict[str, str]
    updated: dict[str, str]


class DotenvImporter:
    """Pure, testable dotenv import logic.

    This class performs:
    - path validation
    - safe file reading
    - dotenv parsing
    - diffing against existing keys

    It performs no UI operations.
    """

    def load_file(self, path: str) -> str:
        """Validate and safely read a dotenv file."""
        source = validate_selected_path(path, must_exist=True)
        return safe_read_text_file(
            source,
            max_bytes=_MAX_BYTES,
            encoding="utf-8",
            errors="replace",
        )

    def parse(self, content: str) -> dict[str, str]:
        """Parse dotenv content into a key/value mapping."""
        return parse_dotenv(content)

    def diff(
        self,
        new_vars: dict[str, str],
        existing: dict[str, str],
    ) -> DotenvImportResult:
        """Compute added and updated variables."""
        added: dict[str, str] = {}
        updated: dict[str, str] = {}

        for key, value in new_vars.items():
            if key in existing:
                if existing[key] != value:
                    updated[key] = value
            else:
                added[key] = value

        return DotenvImportResult(added=added, updated=updated)
