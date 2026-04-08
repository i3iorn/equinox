"""Variable groups management"""

import logging
from typing import List, Dict, Any, Optional

from equinox.storage.database import Database
from equinox.core.exceptions import StorageError, ValidationError, SecurityError, DuplicateError
from equinox.storage.utils import (
    require_positive_int,
    require_str,
    validate_variable_key,
    validate_variable_value,
)

logger = logging.getLogger(__name__)


class VariableGroupManager:
    """Manage variable groups and their items"""

    # Security limits
    MAX_NAME_LENGTH = 200
    MAX_DESCRIPTION_LENGTH = 1000
    MAX_VARIABLE_KEY_LENGTH = 100
    MAX_VARIABLE_VALUE_LENGTH = 10000
    MAX_VARIABLES_PER_GROUP = 100
    MAX_GROUPS = 1000

    def __init__(self, db: Database):
        """Initialize variable group manager

        Args:
            db: Database instance
        """
        self.db = db

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
            description, "Variable group description", self.MAX_DESCRIPTION_LENGTH, required=False
        )

        # Check group count limit
        count_result = self.db.fetchone("SELECT COUNT(*) as count FROM variable_groups")
        if count_result and count_result["count"] >= self.MAX_GROUPS:
            raise SecurityError(f"Maximum number of variable groups reached ({self.MAX_GROUPS})")

        try:
            group_id = self.db.insert(
                "INSERT INTO variable_groups (name, description) VALUES (?, ?)",
                (name, description)
            )
            logger.info("Created variable group %r with ID %d", name, group_id)
            return group_id

        except DuplicateError:
            raise DuplicateError(f"Variable group '{name}' already exists")
        except Exception as e:
            raise StorageError(f"Failed to create variable group: {e}")

    def get_group(self, group_id: int) -> Optional[Dict[str, Any]]:
        """Get variable group by ID

        Args:
            group_id: Group ID

        Returns:
            Group data or None

        Raises:
            ValidationError: If group_id is invalid
        """
        require_positive_int(group_id, "Variable group ID")

        return self.db.fetchone("SELECT * FROM variable_groups WHERE id = ?", (group_id,))

    def list_groups(self) -> List[Dict[str, Any]]:
        """List all variable groups

        Returns:
            List of variable groups
        """
        return self.db.fetchall("SELECT * FROM variable_groups ORDER BY name")

    def update_group(self, group_id: int, name: Optional[str] = None, description: Optional[str] = None) -> None:
        """Update variable group

        Args:
            group_id: Group ID
            name: New group name
            description: New group description

        Raises:
            ValidationError: If input is invalid
            StorageError: If group doesn't exist or update fails
        """
        require_positive_int(group_id, "Variable group ID")

        group = self.get_group(group_id)
        if not group:
            raise StorageError(f"Variable group with ID {group_id} does not exist")

        updates = []
        params = []

        if name is not None:
            name = require_str(name, "Variable group name", self.MAX_NAME_LENGTH)
            updates.append("name = ?")
            params.append(name)

        if description is not None:
            description = require_str(
                description, "Variable group description", self.MAX_DESCRIPTION_LENGTH, required=False
            )
            updates.append("description = ?")
            params.append(description)

        if not updates:
            logger.warning("No updates provided for variable group %d", group_id)
            return

        try:
            updates.append("updated_at = CURRENT_TIMESTAMP")
            params.append(group_id)
            query = f"UPDATE variable_groups SET {', '.join(updates)} WHERE id = ?"
            self.db.execute(query, tuple(params))
            logger.info("Updated variable group %r (ID: %d)", group['name'], group_id)

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
        require_positive_int(group_id, "Variable group ID")

        group = self.get_group(group_id)
        if not group:
            raise StorageError(f"Variable group with ID {group_id} does not exist")

        try:
            # Count variables with a single COUNT query rather than fetching all rows
            count_row = self.db.fetchone(
                "SELECT COUNT(*) AS cnt FROM variable_group_items WHERE group_id = ?",
                (group_id,),
            )
            var_count = (count_row or {}).get("cnt", 0)

            self.db.execute("DELETE FROM variable_groups WHERE id = ?", (group_id,))
            logger.warning(
                "Deleted variable group %r (ID: %d) and %d variable(s)",
                group['name'], group_id, var_count,
            )

        except Exception as e:
            raise StorageError(f"Failed to delete variable group: {e}")

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
            SecurityError: If limits exceeded
            StorageError: If operation fails
        """
        # Validate group_id
        require_positive_int(group_id, "Variable group ID")

        # Check group exists
        group = self.get_group(group_id)
        if not group:
            raise StorageError(f"Variable group with ID {group_id} does not exist")

        key = validate_variable_key(key, self.MAX_VARIABLE_KEY_LENGTH)
        validate_variable_value(value, self.MAX_VARIABLE_VALUE_LENGTH)

        # Validate description
        if not isinstance(description, str):
            raise ValidationError("Variable description must be a string")

        if len(description) > self.MAX_DESCRIPTION_LENGTH:
            raise ValidationError(f"Variable description too long (max {self.MAX_DESCRIPTION_LENGTH} characters)")

        # Check variable count limit
        count_result = self.db.fetchone(
            "SELECT COUNT(*) as count FROM variable_group_items WHERE group_id = ?",
            (group_id,)
        )
        if count_result and count_result["count"] >= self.MAX_VARIABLES_PER_GROUP:
            raise SecurityError(f"Maximum number of variables per group reached ({self.MAX_VARIABLES_PER_GROUP})")

        try:
            # Try to insert, or replace if exists
            var_id = self.db.insert(
                """
                INSERT INTO variable_group_items (group_id, key, value, description)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(group_id, key) DO UPDATE SET
                    value = excluded.value,
                    description = excluded.description,
                    created_at = CURRENT_TIMESTAMP
                """,
                (group_id, key, value, description)
            )
            logger.info("Added/updated variable %r in group %d", key, group_id)
            return var_id

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
        # Validate group_id
        require_positive_int(group_id, "Variable group ID")

        # Validate key
        if not key or not isinstance(key, str):
            raise ValidationError("Variable key must be a non-empty string")

        try:
            self.db.execute(
                "DELETE FROM variable_group_items WHERE group_id = ? AND key = ?",
                (group_id, key)
            )
            logger.info("Removed variable %r from group %d", key, group_id)

        except Exception as e:
            raise StorageError(f"Failed to remove variable: {e}")

    def list_group_variables(self, group_id: int) -> List[Dict[str, Any]]:
        """List all variables in a group

        Args:
            group_id: Group ID

        Returns:
            List of variables

        Raises:
            ValidationError: If group_id is invalid
        """
        require_positive_int(group_id, "Variable group ID")

        return self.db.fetchall(
            "SELECT * FROM variable_group_items WHERE group_id = ? ORDER BY key",
            (group_id,)
        )

    def get_group_variables_dict(self, group_id: int) -> Dict[str, str]:
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
