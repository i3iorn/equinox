"""Variable and variable-group methods for CollectionManager."""

import logging
from typing import Dict, List, Any

from equinox.core.exceptions import StorageError, ValidationError
from equinox.storage.utils import (
    require_positive_int,
    validate_variable_key,
    validate_variable_value,
)

logger = logging.getLogger(__name__)


class CollectionVariablesMixin:
    """Mixin providing variable management for CollectionManager."""

    def add_variable(self, collection_id: int, key: str, value: str, description: str = "") -> int:
        """Add or update a variable for a collection.

        Args:
            collection_id: Collection ID
            key: Variable key
            value: Variable value
            description: Variable description

        Returns:
            Variable ID

        Raises:
            ValidationError: If input is invalid
            StorageError: If operation fails
        """
        require_positive_int(collection_id, "Collection ID")
        if not self.get_collection(collection_id):
            raise StorageError(f"Collection with ID {collection_id} does not exist")
        key = validate_variable_key(key)
        validate_variable_value(value)
        if not isinstance(description, str):
            raise ValidationError("Variable description must be a string")
        if len(description) > self.MAX_DESCRIPTION_LENGTH:
            raise ValidationError(f"Variable description too long (max {self.MAX_DESCRIPTION_LENGTH} characters)")

        try:
            var_id = self.db.insert(
                """
                INSERT INTO collection_variables (collection_id, key, value, description)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(collection_id, key) DO UPDATE SET
                    value = excluded.value,
                    description = excluded.description,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (collection_id, key, value, description)
            )
            logger.info(f"Added/updated variable '{key}' for collection {collection_id}")
            return var_id
        except Exception as exc:
            raise StorageError(f"Failed to add variable: {exc}")

    def remove_variable(self, collection_id: int, key: str) -> None:
        """Remove a variable from a collection.

        Args:
            collection_id: Collection ID
            key: Variable key

        Raises:
            ValidationError: If input is invalid
            StorageError: If deletion fails
        """
        require_positive_int(collection_id, "Collection ID")
        if not key or not isinstance(key, str):
            raise ValidationError("Variable key must be a non-empty string")

        try:
            self.db.execute(
                "DELETE FROM collection_variables WHERE collection_id = ? AND key = ?",
                (collection_id, key)
            )
            logger.info(f"Removed variable '{key}' from collection {collection_id}")
        except Exception as exc:
            raise StorageError(f"Failed to remove variable: {exc}")

    def list_collection_variables(self, collection_id: int) -> List[Dict[str, Any]]:
        """List all variables for a collection.

        Args:
            collection_id: Collection ID

        Returns:
            List of variables

        Raises:
            ValidationError: If collection_id is invalid
        """
        require_positive_int(collection_id, "Collection ID")
        return self.db.fetchall(
            "SELECT * FROM collection_variables WHERE collection_id = ? ORDER BY key",
            (collection_id,)
        )

    def get_collection_variables_dict(self, collection_id: int) -> Dict[str, str]:
        """Get collection variables as a key-value dictionary."""
        variables = self.list_collection_variables(collection_id)
        return {var["key"]: var["value"] for var in variables}

    def add_variable_group(self, collection_id: int, group_id: int, priority: int = 0) -> int:
        """Add a variable group to a collection.

        Args:
            collection_id: Collection ID
            group_id: Variable group ID
            priority: Priority (lower = higher priority)

        Returns:
            Association ID

        Raises:
            ValidationError: If input is invalid
            StorageError: If operation fails
        """
        require_positive_int(collection_id, "Collection ID")
        require_positive_int(group_id, "Variable group ID")
        if not isinstance(priority, int):
            raise ValidationError("Priority must be an integer")

        try:
            assoc_id = self.db.insert(
                """
                INSERT INTO collection_variable_groups (collection_id, group_id, priority)
                VALUES (?, ?, ?)
                ON CONFLICT(collection_id, group_id) DO UPDATE SET
                    priority = excluded.priority
                """,
                (collection_id, group_id, priority)
            )
            logger.info(f"Added variable group {group_id} to collection {collection_id}")
            return assoc_id
        except Exception as exc:
            raise StorageError(f"Failed to add variable group to collection: {exc}")

    def remove_variable_group(self, collection_id: int, group_id: int) -> None:
        """Remove a variable group from a collection.

        Args:
            collection_id: Collection ID
            group_id: Variable group ID

        Raises:
            ValidationError: If input is invalid
            StorageError: If deletion fails
        """
        require_positive_int(collection_id, "Collection ID")
        require_positive_int(group_id, "Variable group ID")

        try:
            self.db.execute(
                "DELETE FROM collection_variable_groups WHERE collection_id = ? AND group_id = ?",
                (collection_id, group_id)
            )
            logger.info(f"Removed variable group {group_id} from collection {collection_id}")
        except Exception as exc:
            raise StorageError(f"Failed to remove variable group from collection: {exc}")

    def list_collection_variable_groups(self, collection_id: int) -> List[Dict[str, Any]]:
        """List all variable groups associated with a collection.

        Args:
            collection_id: Collection ID

        Returns:
            List of variable groups with priority

        Raises:
            ValidationError: If collection_id is invalid
        """
        require_positive_int(collection_id, "Collection ID")
        return self.db.fetchall(
            """
            SELECT vg.*, cvg.priority
            FROM variable_groups vg
            JOIN collection_variable_groups cvg ON vg.id = cvg.group_id
            WHERE cvg.collection_id = ?
            ORDER BY cvg.priority, vg.name
            """,
            (collection_id,)
        )

    def get_all_collection_variables(self, collection_id: int) -> Dict[str, str]:
        """Get all variables for a collection (from groups + collection-specific).

        Variable precedence (highest to lowest):
        1. Collection-specific variables
        2. Variable groups (by priority, lower number = higher priority)

        Args:
            collection_id: Collection ID

        Returns:
            Merged dictionary of all variables

        Raises:
            ValidationError: If collection_id is invalid
        """
        require_positive_int(collection_id, "Collection ID")

        merged: Dict[str, str] = {}

        # Reverse so lower priority numbers are processed last and thus override.
        groups = self.list_collection_variable_groups(collection_id)
        for group in reversed(groups):
            group_vars = self.db.fetchall(
                "SELECT key, value FROM variable_group_items WHERE group_id = ?",
                (group["id"],)
            )
            for var in group_vars:
                merged[var["key"]] = var["value"]

        merged.update(self.get_collection_variables_dict(collection_id))
        return merged
