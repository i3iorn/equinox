"""Variable groups management"""

import logging
from typing import Any

from equinox.core.exceptions import DuplicateError
from equinox.core.exceptions import SecurityError
from equinox.core.exceptions import StorageError
from equinox.storage.database import Database
from equinox.storage.utils import (
    MAX_DESCRIPTION_LENGTH as _MAX_DESC,
)
from equinox.storage.utils import (
    MAX_NAME_LENGTH as _MAX_NAME,
)
from equinox.storage.utils import (
    MAX_VARIABLE_KEY_LENGTH as _MAX_VAR_KEY,
)
from equinox.storage.utils import (
    MAX_VARIABLE_VALUE_LENGTH as _MAX_VAR_VAL,
)
from equinox.storage.utils import require_positive_int
from equinox.storage.utils import require_str
from equinox.storage.utils import validate_variable_key
from equinox.storage.utils import validate_variable_value

logger = logging.getLogger(__name__)


class VariableGroupManager:
    """Manage variable groups and their items"""

    # Security limits — delegated to central constants in storage.utils
    MAX_NAME_LENGTH = _MAX_NAME
    MAX_DESCRIPTION_LENGTH = _MAX_DESC
    MAX_VARIABLE_KEY_LENGTH = _MAX_VAR_KEY
    MAX_VARIABLE_VALUE_LENGTH = _MAX_VAR_VAL
    MAX_VARIABLES_PER_GROUP = 100
    MAX_GROUPS = 1000

    def __init__(self, db: Database):
        """Initialize variable group manager

        Args:
            db: Database instance
        """
        self.db = db

    # ── Private helpers ───────────────────────────────────────────────────────

    def _require_group(self, group_id: int) -> dict[str, Any]:
        """Validate *group_id* and return the matching group row.

        Centralises the repeated pattern of: validate the ID, fetch the row,
        and raise a consistent error when it is absent.

        Args:
            group_id: The group primary key to look up.

        Returns:
            The group row as a dict.

        Raises:
            ValidationError: If *group_id* is not a positive integer.
            StorageError: If no group with that ID exists.
        """
        require_positive_int(group_id, "Variable group ID")
        group = self.db.fetchone("SELECT * FROM variable_groups WHERE id = ?", (group_id,))
        if not group:
            raise StorageError(f"Variable group with ID {group_id} does not exist")
        return group

    # ── Group CRUD ────────────────────────────────────────────────────────────

    def create_group(self, name: str, description: str = "") -> int:
        """Create a new variable group

        Args:
            name: Group name
            description: Group description

        Returns:
            Group ID

        Raises:
            ValidationError: If input is invalid
            SecurityError: If limits exceeded
            StorageError: If creation fails
        """
        name = require_str(name, "Variable group name", self.MAX_NAME_LENGTH)
        description = require_str(
            description,
            "Variable group description",
            self.MAX_DESCRIPTION_LENGTH,
            required=False,
        )

        count_result = self.db.fetchone("SELECT COUNT(*) as count FROM variable_groups")
        if count_result and count_result["count"] >= self.MAX_GROUPS:
            raise SecurityError(f"Maximum number of variable groups reached ({self.MAX_GROUPS})")

        try:
            group_id = self.db.insert(
                "INSERT INTO variable_groups (name, description) VALUES (?, ?)",
                (name, description),
            )
            logger.info("Created variable group %r with ID %d", name, group_id)
            return int(group_id)

        except DuplicateError:
            raise DuplicateError(f"Variable group '{name}' already exists")
        except Exception as e:
            raise StorageError(f"Failed to create variable group: {e}")

    def get_group(self, group_id: int) -> dict[str, Any] | None:
        """Get variable group by ID

        Args:
            group_id: Group ID

        Returns:
            Group data or None if not found

        Raises:
            ValidationError: If group_id is invalid
        """
        require_positive_int(group_id, "Variable group ID")
        row = self.db.fetchone("SELECT * FROM variable_groups WHERE id = ?", (group_id,))
        return row

    def list_groups(self) -> list[dict[str, Any]]:
        """List all variable groups

        Returns:
            List of variable groups
        """
        rows = self.db.fetchall("SELECT * FROM variable_groups ORDER BY name")
        return rows

    def update_group(
        self,
        group_id: int,
        name: str | None = None,
        description: str | None = None,
    ) -> None:
        """Update variable group

        Args:
            group_id: Group ID
            name: New group name
            description: New group description

        Raises:
            ValidationError: If input is invalid
            StorageError: If group doesn't exist or update fails
        """
        group = self._require_group(group_id)

        updates = []
        params: list[Any] = []

        if name is not None:
            name = require_str(name, "Variable group name", self.MAX_NAME_LENGTH)
            updates.append("name = ?")
            params.append(name)

        if description is not None:
            description = require_str(
                description,
                "Variable group description",
                self.MAX_DESCRIPTION_LENGTH,
                required=False,
            )
            updates.append("description = ?")
            params.append(description)

        if not updates:
            logger.warning("No updates provided for variable group %d", group_id)
            return

        try:
            updates.append("updated_at = CURRENT_TIMESTAMP")
            params.append(group_id)
            # updates contains only hardcoded "col = ?" literals — no user data in the SQL string.
            sql = "UPDATE variable_groups SET " + ", ".join(updates) + " WHERE id = ?"  # nosec B608
            self.db.execute(sql, tuple(params))
            logger.info("Updated variable group %r (ID: %d)", group["name"], group_id)

        except DuplicateError:
            raise DuplicateError(f"Variable group name '{name}' already exists")
        except Exception as e:
            raise StorageError(f"Failed to update variable group: {e}")

    def delete_group(self, group_id: int) -> None:
        """Delete variable group and all its variables

        Args:
            group_id: Group ID

        Raises:
            ValidationError: If group_id is invalid
            StorageError: If group doesn't exist or deletion fails
        """
        group = self._require_group(group_id)

        try:
            count_row = self.db.fetchone(
                "SELECT COUNT(*) AS cnt FROM variable_group_items WHERE group_id = ?",
                (group_id,),
            )
            var_count = (count_row or {}).get("cnt", 0)

            self.db.execute("DELETE FROM variable_groups WHERE id = ?", (group_id,))
            logger.warning(
                "Deleted variable group %r (ID: %d) and %d variable(s)",
                group["name"],
                group_id,
                var_count,
            )

        except Exception as e:
            raise StorageError(f"Failed to delete variable group: {e}")

    # ── Variable CRUD ─────────────────────────────────────────────────────────

    def add_variable(self, group_id: int, key: str, value: str, description: str = "") -> int:
        """Add or update a variable in a group

        Args:
            group_id: Group ID
            key: Variable key
            value: Variable value
            description: Variable description

        Returns:
            Variable ID

        Raises:
            ValidationError: If input is invalid
            SecurityError: If the per-group variable limit is exceeded on a new key
            StorageError: If operation fails
        """
        self._require_group(group_id)

        key = validate_variable_key(key, self.MAX_VARIABLE_KEY_LENGTH)
        validate_variable_value(value, self.MAX_VARIABLE_VALUE_LENGTH)
        description = require_str(
            description,
            "Variable description",
            self.MAX_DESCRIPTION_LENGTH,
            required=False,
        )

        count_result = self.db.fetchone(
            "SELECT COUNT(*) as count FROM variable_group_items WHERE group_id = ?",
            (group_id,),
        )
        if count_result and count_result["count"] >= self.MAX_VARIABLES_PER_GROUP:
            existing = self.db.fetchone(
                "SELECT id FROM variable_group_items WHERE group_id = ? AND key = ?",
                (group_id, key),
            )
            if not existing:
                raise SecurityError(
                    f"Maximum number of variables per group reached ({self.MAX_VARIABLES_PER_GROUP})",
                )

        try:
            var_id = self.db.insert(
                """
                INSERT INTO variable_group_items (group_id, key, value, description)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(group_id, key) DO UPDATE SET
                    value = excluded.value,
                    description = excluded.description,
                    created_at = CURRENT_TIMESTAMP
                """,
                (group_id, key, value, description),
            )
            logger.info("Added/updated variable %r in group %d", key, group_id)
            return int(var_id)

        except Exception as e:
            raise StorageError(f"Failed to add variable: {e}")

    def remove_variable(self, group_id: int, key: str) -> None:
        """Remove a variable from a group

        Args:
            group_id: Group ID
            key: Variable key

        Raises:
            ValidationError: If input is invalid
            StorageError: If variable doesn't exist or deletion fails
        """
        require_positive_int(group_id, "Variable group ID")
        key = validate_variable_key(key, self.MAX_VARIABLE_KEY_LENGTH)

        try:
            cursor = self.db.execute(
                "DELETE FROM variable_group_items WHERE group_id = ? AND key = ?",
                (group_id, key),
            )
            if cursor.rowcount == 0:
                raise StorageError(f"Variable '{key}' not found in group {group_id}")
            logger.info("Removed variable %r from group %d", key, group_id)

        except StorageError:
            raise
        except Exception as e:
            raise StorageError(f"Failed to remove variable: {e}")

    def list_group_variables(self, group_id: int) -> list[dict[str, Any]]:
        """List all variables in a group

        Args:
            group_id: Group ID

        Returns:
            List of variables

        Raises:
            ValidationError: If group_id is invalid
        """
        require_positive_int(group_id, "Variable group ID")

        rows = self.db.fetchall(
            "SELECT * FROM variable_group_items WHERE group_id = ? ORDER BY key",
            (group_id,),
        )
        return rows

    def get_group_variables_dict(self, group_id: int) -> dict[str, str]:
        """Get group variables as a dictionary

        Args:
            group_id: Group ID

        Returns:
            Dictionary of key-value pairs

        Raises:
            ValidationError: If group_id is invalid
        """
        variables = self.list_group_variables(group_id)
        return {var["key"]: var["value"] for var in variables}
