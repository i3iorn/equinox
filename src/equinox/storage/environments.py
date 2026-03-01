"""Environment management"""

import json
import logging
import re
from typing import List, Dict, Any, Optional

from equinox.storage.database import Database
from equinox.core.exceptions import StorageError, ValidationError, SecurityError

logger = logging.getLogger(__name__)


class EnvironmentManager:
    """Manage environments and variables"""

    # Security limits
    MAX_NAME_LENGTH = 200
    MAX_DESCRIPTION_LENGTH = 1000
    MAX_VARIABLE_COUNT = 100
    MAX_VARIABLE_KEY_LENGTH = 100
    MAX_VARIABLE_VALUE_LENGTH = 10000
    MAX_ENVIRONMENTS = 1000

    # Variable name validation pattern (alphanumeric, underscore, dash)
    VARIABLE_NAME_PATTERN = re.compile(r'^[a-zA-Z0-9_-]+$')

    def __init__(self, db: Database):
        """
        Initialize environment manager

        Args:
            db: Database instance
        """
        self.db = db

    def create_environment(
        self, name: str, variables: Dict[str, str], description: str = ""
    ) -> int:
        """
        Create a new environment

        Args:
            name: Environment name
            variables: Environment variables as key-value pairs
            description: Environment description

        Returns:
            Environment ID

        Raises:
            ValidationError: If input is invalid
            SecurityError: If limits exceeded
            StorageError: If creation fails
        """
        # Validate name
        if not name or not isinstance(name, str):
            raise ValidationError("Environment name must be a non-empty string")

        if len(name) > self.MAX_NAME_LENGTH:
            raise ValidationError(f"Environment name too long (max {self.MAX_NAME_LENGTH} characters)")

        name = name.strip()
        if not name:
            raise ValidationError("Environment name cannot be empty or whitespace")

        # Validate description
        if not isinstance(description, str):
            raise ValidationError("Environment description must be a string")

        if len(description) > self.MAX_DESCRIPTION_LENGTH:
            raise ValidationError(f"Environment description too long (max {self.MAX_DESCRIPTION_LENGTH} characters)")

        # Validate variables
        if not isinstance(variables, dict):
            raise ValidationError("Variables must be a dictionary")

        if len(variables) > self.MAX_VARIABLE_COUNT:
            raise SecurityError(f"Too many variables (max {self.MAX_VARIABLE_COUNT})")

        # Check environment count limit
        env_count = self.db.fetchone("SELECT COUNT(*) as count FROM environments")
        if env_count and env_count["count"] >= self.MAX_ENVIRONMENTS:
            raise SecurityError(f"Maximum number of environments reached ({self.MAX_ENVIRONMENTS})")

        # Validate each variable
        sanitized_variables = {}
        for key, value in variables.items():
            # Validate key
            if not isinstance(key, str):
                raise ValidationError(f"Variable key must be a string: {key}")

            if not key or not key.strip():
                raise ValidationError("Variable key cannot be empty")

            if len(key) > self.MAX_VARIABLE_KEY_LENGTH:
                raise ValidationError(f"Variable key too long: {key} (max {self.MAX_VARIABLE_KEY_LENGTH} characters)")

            if not self.VARIABLE_NAME_PATTERN.match(key):
                raise ValidationError(
                    f"Invalid variable key: {key}. Must contain only alphanumeric characters, "
                    "underscores, and hyphens"
                )

            # Validate value
            if not isinstance(value, str):
                raise ValidationError(f"Variable value must be a string for key: {key}")

            if len(value) > self.MAX_VARIABLE_VALUE_LENGTH:
                raise ValidationError(
                    f"Variable value too long for key '{key}' (max {self.MAX_VARIABLE_VALUE_LENGTH} characters)"
                )

            sanitized_variables[key] = value

        try:
            environment_id = self.db.insert(
                "INSERT INTO environments (name, description, variables) VALUES (?, ?, ?)",
                (name, description, json.dumps(sanitized_variables)),
            )
            logger.info(f"Created environment '{name}' with ID {environment_id} and {len(sanitized_variables)} variables")
            return environment_id

        except Exception as e:
            # Check for unique constraint violation
            if "UNIQUE constraint failed" in str(e):
                raise StorageError(f"Environment '{name}' already exists")
            raise StorageError(f"Failed to create environment: {e}")

    def get_environment(self, environment_id: int) -> Optional[Dict[str, Any]]:
        """
        Get environment by ID

        Args:
            environment_id: Environment ID

        Returns:
            Environment data or None

        Raises:
            ValidationError: If environment_id is invalid
        """
        if not isinstance(environment_id, int) or environment_id <= 0:
            raise ValidationError("Environment ID must be a positive integer")

        row = self.db.fetchone("SELECT * FROM environments WHERE id = ?", (environment_id,))
        if row:
            row = dict(row)
            try:
                row["variables"] = json.loads(row["variables"])
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse variables for environment {environment_id}: {e}")
                row["variables"] = {}
            try:
                row["secret_keys"] = json.loads(row.get("secret_keys") or "[]")
            except Exception:
                row["secret_keys"] = []
        return row

    def get_active_environment(self) -> Optional[Dict[str, Any]]:
        """Get the currently active environment"""
        row = self.db.fetchone("SELECT * FROM environments WHERE is_active = 1")
        if row:
            row = dict(row)
            row["variables"] = json.loads(row["variables"])
            try:
                row["secret_keys"] = json.loads(row.get("secret_keys") or "[]")
            except Exception:
                row["secret_keys"] = []
        return row

    def list_environments(self) -> List[Dict[str, Any]]:
        """
        List all environments

        Returns:
            List of environments
        """
        rows = self.db.fetchall("SELECT * FROM environments ORDER BY name")
        for row in rows:
            try:
                row["variables"] = json.loads(row["variables"])
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse variables for environment {row['id']}: {e}")
                row["variables"] = {}
            try:
                row["secret_keys"] = json.loads(row.get("secret_keys") or "[]")
            except Exception:
                row["secret_keys"] = []
        return rows

    def update_environment(
        self,
        environment_id: int,
        name: Optional[str] = None,
        variables: Optional[Dict[str, str]] = None,
        description: Optional[str] = None,
        secret_keys: Optional[List[str]] = None,
    ) -> None:
        """Update environment

        Args:
            environment_id: Environment ID
            name: New environment name
            variables: New environment variables
            description: New environment description

        Raises:
            ValidationError: If input is invalid
            SecurityError: If limits exceeded
            StorageError: If environment doesn't exist or update fails
        """
        # Validate environment_id
        if not isinstance(environment_id, int) or environment_id <= 0:
            raise ValidationError("Environment ID must be a positive integer")

        # Check environment exists
        environment = self.get_environment(environment_id)
        if not environment:
            raise StorageError(f"Environment with ID {environment_id} does not exist")

        updates = []
        params = []

        # Validate and add name update
        if name is not None:
            if not isinstance(name, str):
                raise ValidationError("Environment name must be a string")

            if len(name) > self.MAX_NAME_LENGTH:
                raise ValidationError(f"Environment name too long (max {self.MAX_NAME_LENGTH} characters)")

            name = name.strip()
            if not name:
                raise ValidationError("Environment name cannot be empty or whitespace")

            updates.append("name = ?")
            params.append(name)

        # Validate and add description update
        if description is not None:
            if not isinstance(description, str):
                raise ValidationError("Environment description must be a string")

            if len(description) > self.MAX_DESCRIPTION_LENGTH:
                raise ValidationError(f"Environment description too long (max {self.MAX_DESCRIPTION_LENGTH} characters)")

            updates.append("description = ?")
            params.append(description)

        # Validate and add variables update
        if variables is not None:
            if not isinstance(variables, dict):
                raise ValidationError("Variables must be a dictionary")

            if len(variables) > self.MAX_VARIABLE_COUNT:
                raise SecurityError(f"Too many variables (max {self.MAX_VARIABLE_COUNT})")

            # Validate each variable
            sanitized_variables = {}
            for key, value in variables.items():
                if not isinstance(key, str):
                    raise ValidationError(f"Variable key must be a string: {key}")

                if not key or not key.strip():
                    raise ValidationError("Variable key cannot be empty")

                if len(key) > self.MAX_VARIABLE_KEY_LENGTH:
                    raise ValidationError(f"Variable key too long: {key} (max {self.MAX_VARIABLE_KEY_LENGTH} characters)")

                if not self.VARIABLE_NAME_PATTERN.match(key):
                    raise ValidationError(
                        f"Invalid variable key: {key}. Must contain only alphanumeric characters, "
                        "underscores, and hyphens"
                    )

                if not isinstance(value, str):
                    raise ValidationError(f"Variable value must be a string for key: {key}")

                if len(value) > self.MAX_VARIABLE_VALUE_LENGTH:
                    raise ValidationError(
                        f"Variable value too long for key '{key}' (max {self.MAX_VARIABLE_VALUE_LENGTH} characters)"
                    )

                sanitized_variables[key] = value

            updates.append("variables = ?")
            params.append(json.dumps(sanitized_variables))

        if secret_keys is not None:
            if not isinstance(secret_keys, list):
                raise ValidationError("secret_keys must be a list")
            updates.append("secret_keys = ?")
            params.append(json.dumps(list(secret_keys)))

        if not updates:
            logger.warning(f"No updates provided for environment {environment_id}")
            return

        try:
            updates.append("updated_at = CURRENT_TIMESTAMP")
            params.append(environment_id)
            query = f"UPDATE environments SET {', '.join(updates)} WHERE id = ?"
            self.db.execute(query, tuple(params))
            logger.info(f"Updated environment '{environment['name']}' (ID: {environment_id})")

        except Exception as e:
            if "UNIQUE constraint failed" in str(e):
                raise StorageError(f"Environment name '{name}' already exists")
            raise StorageError(f"Failed to update environment: {e}")

    def set_active_environment(self, environment_id: int) -> None:
        """Set active environment

        Args:
            environment_id: Environment ID to activate

        Raises:
            ValidationError: If environment_id is invalid
            StorageError: If environment doesn't exist
        """
        # Validate environment_id
        if not isinstance(environment_id, int) or environment_id <= 0:
            raise ValidationError("Environment ID must be a positive integer")

        # Check environment exists
        environment = self.get_environment(environment_id)
        if not environment:
            raise StorageError(f"Environment with ID {environment_id} does not exist")

        try:
            # Use a single UPDATE with CASE to make the switch atomic
            self.db.execute(
                "UPDATE environments SET is_active = CASE WHEN id = ? THEN 1 ELSE 0 END",
                (environment_id,),
            )
            logger.info(f"Activated environment '{environment['name']}' (ID: {environment_id})")

        except Exception as e:
            raise StorageError(f"Failed to activate environment: {e}")

    def delete_environment(self, environment_id: int) -> None:
        """Delete environment

        Args:
            environment_id: Environment ID to delete

        Raises:
            ValidationError: If environment_id is invalid
            StorageError: If environment doesn't exist or deletion fails
        """
        # Validate environment_id
        if not isinstance(environment_id, int) or environment_id <= 0:
            raise ValidationError("Environment ID must be a positive integer")

        # Check environment exists
        environment = self.get_environment(environment_id)
        if not environment:
            raise StorageError(f"Environment with ID {environment_id} does not exist")

        try:
            self.db.execute("DELETE FROM environments WHERE id = ?", (environment_id,))
            logger.warning(f"Deleted environment '{environment['name']}' (ID: {environment_id})")

        except Exception as e:
            raise StorageError(f"Failed to delete environment: {e}")

    def interpolate_variables(self, text: str, max_iterations: int = 10) -> str:
        """
        Replace {{variable}} placeholders with values from active environment

        Args:
            text: Text with {{variable}} placeholders
            max_iterations: Maximum number of interpolation passes (prevents infinite loops)

        Returns:
            Text with variables replaced

        Raises:
            ValidationError: If input is invalid
            SecurityError: If interpolation would cause infinite loop
        """
        if not isinstance(text, str):
            raise ValidationError("Text must be a string")

        if len(text) > 1_000_000:  # 1MB text limit
            raise SecurityError("Text too large for variable interpolation")

        env = self.get_active_environment()
        if not env:
            return text

        variables = env.get("variables", {})
        if not variables:
            return text

        # Perform interpolation with iteration limit to prevent infinite loops
        # (e.g., if variable values contain references to other variables)
        iteration = 0
        original_text = text

        while iteration < max_iterations:
            iteration += 1
            previous_text = text

            # Replace variables
            for key, value in variables.items():
                placeholder = f"{{{{{key}}}}}"
                if placeholder in text:
                    text = text.replace(placeholder, value)

            # If no changes were made, we're done
            if text == previous_text:
                break

            # Check for excessive expansion (potential DoS)
            if len(text) > len(original_text) * 100:
                raise SecurityError("Variable interpolation caused excessive text expansion")

        if iteration >= max_iterations:
            logger.warning(f"Variable interpolation reached max iterations ({max_iterations})")

        return text
