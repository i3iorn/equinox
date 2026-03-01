"""Variable groups management"""

import logging
from typing import List, Dict, Any, Optional

from equinox.storage.database import Database
from equinox.core.exceptions import StorageError, ValidationError, SecurityError

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
        # Validate name
        if not name or not isinstance(name, str):
            raise ValidationError("Variable group name must be a non-empty string")

        if len(name) > self.MAX_NAME_LENGTH:
            raise ValidationError(f"Variable group name too long (max {self.MAX_NAME_LENGTH} characters)")

        name = name.strip()
        if not name:
            raise ValidationError("Variable group name cannot be empty or whitespace")

        # Validate description
        if not isinstance(description, str):
            raise ValidationError("Variable group description must be a string")

        if len(description) > self.MAX_DESCRIPTION_LENGTH:
            raise ValidationError(f"Variable group description too long (max {self.MAX_DESCRIPTION_LENGTH} characters)")

        # Check group count limit
        count_result = self.db.fetchone("SELECT COUNT(*) as count FROM variable_groups")
        if count_result and count_result["count"] >= self.MAX_GROUPS:
            raise SecurityError(f"Maximum number of variable groups reached ({self.MAX_GROUPS})")

        try:
            group_id = self.db.insert(
                "INSERT INTO variable_groups (name, description) VALUES (?, ?)",
                (name, description)
            )
            logger.info(f"Created variable group '{name}' with ID {group_id}")
            return group_id

        except Exception as e:
            if "UNIQUE constraint failed" in str(e):
                raise StorageError(f"Variable group '{name}' already exists")
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
        if not isinstance(group_id, int) or group_id <= 0:
            raise ValidationError("Variable group ID must be a positive integer")

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
        # Validate group_id
        if not isinstance(group_id, int) or group_id <= 0:
            raise ValidationError("Variable group ID must be a positive integer")

        # Check group exists
        group = self.get_group(group_id)
        if not group:
            raise StorageError(f"Variable group with ID {group_id} does not exist")

        updates = []
        params = []

        if name is not None:
            if not isinstance(name, str):
                raise ValidationError("Variable group name must be a string")

            if len(name) > self.MAX_NAME_LENGTH:
                raise ValidationError(f"Variable group name too long (max {self.MAX_NAME_LENGTH} characters)")

            name = name.strip()
            if not name:
                raise ValidationError("Variable group name cannot be empty or whitespace")

            updates.append("name = ?")
            params.append(name)

        if description is not None:
            if not isinstance(description, str):
                raise ValidationError("Variable group description must be a string")

            if len(description) > self.MAX_DESCRIPTION_LENGTH:
                raise ValidationError(f"Variable group description too long (max {self.MAX_DESCRIPTION_LENGTH} characters)")

            updates.append("description = ?")
            params.append(description)

        if not updates:
            logger.warning(f"No updates provided for variable group {group_id}")
            return

        try:
            updates.append("updated_at = CURRENT_TIMESTAMP")
            params.append(group_id)
            query = f"UPDATE variable_groups SET {', '.join(updates)} WHERE id = ?"
            self.db.execute(query, tuple(params))
            logger.info(f"Updated variable group '{group['name']}' (ID: {group_id})")

        except Exception as e:
            if "UNIQUE constraint failed" in str(e):
                raise StorageError(f"Variable group name '{name}' already exists")
            raise StorageError(f"Failed to update variable group: {e}")

    def delete_group(self, group_id: int) -> None:
        """Delete variable group and all its variables

        Args:
            group_id: Group ID

        Raises:
            ValidationError: If group_id is invalid
            StorageError: If group doesn't exist or deletion fails
        """
        # Validate group_id
        if not isinstance(group_id, int) or group_id <= 0:
            raise ValidationError("Variable group ID must be a positive integer")

        # Check group exists
        group = self.get_group(group_id)
        if not group:
            raise StorageError(f"Variable group with ID {group_id} does not exist")

        try:
            # Count variables
            variables = self.list_group_variables(group_id)
            var_count = len(variables)

            self.db.execute("DELETE FROM variable_groups WHERE id = ?", (group_id,))
            logger.warning(f"Deleted variable group '{group['name']}' (ID: {group_id}) and {var_count} variable(s)")

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
        if not isinstance(group_id, int) or group_id <= 0:
            raise ValidationError("Variable group ID must be a positive integer")

        # Check group exists
        group = self.get_group(group_id)
        if not group:
            raise StorageError(f"Variable group with ID {group_id} does not exist")

        # Validate key
        if not key or not isinstance(key, str):
            raise ValidationError("Variable key must be a non-empty string")

        if len(key) > self.MAX_VARIABLE_KEY_LENGTH:
            raise ValidationError(f"Variable key too long (max {self.MAX_VARIABLE_KEY_LENGTH} characters)")

        key = key.strip()
        if not key:
            raise ValidationError("Variable key cannot be empty or whitespace")

        # Validate value
        if not isinstance(value, str):
            raise ValidationError("Variable value must be a string")

        if len(value) > self.MAX_VARIABLE_VALUE_LENGTH:
            raise ValidationError(f"Variable value too long (max {self.MAX_VARIABLE_VALUE_LENGTH} characters)")

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
            logger.info(f"Added/updated variable '{key}' in group {group_id}")
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
        if not isinstance(group_id, int) or group_id <= 0:
            raise ValidationError("Variable group ID must be a positive integer")

        # Validate key
        if not key or not isinstance(key, str):
            raise ValidationError("Variable key must be a non-empty string")

        try:
            self.db.execute(
                "DELETE FROM variable_group_items WHERE group_id = ? AND key = ?",
                (group_id, key)
            )
            logger.info(f"Removed variable '{key}' from group {group_id}")

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
        if not isinstance(group_id, int) or group_id <= 0:
            raise ValidationError("Variable group ID must be a positive integer")

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
