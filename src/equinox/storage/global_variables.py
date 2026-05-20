"""Global variable storage manager."""

from __future__ import annotations

import logging
from typing import Any

from equinox.core.exceptions import StorageError, ValidationError
from equinox.storage.utils import validate_variable_key, validate_variable_value

logger = logging.getLogger(__name__)


class GlobalVariablesManager:
    """CRUD operations for app-wide global interpolation variables."""

    MAX_DESCRIPTION_LENGTH = 1000

    def __init__(self, db) -> None:
        self.db = db

    def set_variable(self, key: str, value: str, description: str = "") -> int:
        """Create or update a global variable."""
        key = validate_variable_key(key)
        value = validate_variable_value(value)
        if not isinstance(description, str):
            raise ValidationError("Variable description must be a string")
        if len(description) > self.MAX_DESCRIPTION_LENGTH:
            raise ValidationError(
                f"Variable description too long (max {self.MAX_DESCRIPTION_LENGTH} characters)"
            )

        try:
            row_id = self.db.insert(
                """
                INSERT INTO global_variables (key, value, description)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    description = excluded.description,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (key, value, description),
            )
            logger.info("Set global variable %r", key)
            return row_id
        except Exception as exc:
            raise StorageError(f"Failed to set global variable: {exc}") from exc

    def remove_variable(self, key: str) -> None:
        """Delete a global variable by key."""
        key = validate_variable_key(key)
        try:
            self.db.execute("DELETE FROM global_variables WHERE key = ?", (key,))
            logger.info("Removed global variable %r", key)
        except Exception as exc:
            raise StorageError(f"Failed to remove global variable: {exc}") from exc

    def list_variables(self) -> list[dict[str, Any]]:
        """Return all global variables sorted by key."""
        return self.db.fetchall("SELECT * FROM global_variables ORDER BY key")

    def get_variables_dict(self) -> dict[str, str]:
        """Return global variables as key/value mapping."""
        rows = self.list_variables()
        return {row["key"]: row["value"] for row in rows}

    def get_variable(self, key: str) -> dict[str, Any] | None:
        """Return a single global variable row by key."""
        key = validate_variable_key(key)
        return self.db.fetchone("SELECT * FROM global_variables WHERE key = ?", (key,))
