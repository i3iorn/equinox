"""Environment management"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from equinox.core.exceptions import DuplicateError, SecurityError, StorageError, ValidationError
from equinox.core.interpolation import VariableInterpolator
from equinox.storage.database import Database
from equinox.storage.utils import (
    MAX_NAME_LENGTH as _MAX_NAME,
    MAX_DESCRIPTION_LENGTH as _MAX_DESC,
    MAX_VARIABLE_KEY_LENGTH as _MAX_VAR_KEY,
    MAX_VARIABLE_VALUE_LENGTH as _MAX_VAR_VAL,
    require_positive_int, safe_json_dumps, safe_json_loads,
    validate_variable_key, validate_variable_value,
)

logger = logging.getLogger(__name__)

# Variable-name character-set pattern (alphanumeric, underscore, dash).
_VAR_NAME_RE = re.compile(r'^[a-zA-Z0-9_-]+$')


class EnvironmentManager:
    """Manage environments and variables"""

    # Security limits — delegated to central constants in storage.utils
    MAX_NAME_LENGTH = _MAX_NAME
    MAX_DESCRIPTION_LENGTH = _MAX_DESC
    MAX_VARIABLE_COUNT = 100
    MAX_VARIABLE_KEY_LENGTH = _MAX_VAR_KEY
    MAX_VARIABLE_VALUE_LENGTH = _MAX_VAR_VAL
    MAX_ENVIRONMENTS = 1000
    MAX_SECRET_KEYS = 100
    MAX_TEXT_SIZE = 1_000_000  # 1 MB text limit for interpolation
    MAX_EXPANSION_RATIO = 150  # Maximum allowed text expansion during interpolation

    def __init__(self, db: Database) -> None:
        self.db = db

    def _validate_variables(self, variables: dict) -> Dict[str, str]:
        """Validate and return a sanitised copy of a variable dict.

        Raises:
            ValidationError: If any key or value is invalid.
            SecurityError: If the number of variables exceeds the limit.
        """
        if not isinstance(variables, dict):
            raise ValidationError("Variables must be a dictionary")
        if len(variables) > self.MAX_VARIABLE_COUNT:
            raise SecurityError(f"Too many variables (max {self.MAX_VARIABLE_COUNT})")

        sanitized: Dict[str, str] = {}
        for key, value in variables.items():
            key = validate_variable_key(key, self.MAX_VARIABLE_KEY_LENGTH)
            if not _VAR_NAME_RE.match(key):
                raise ValidationError(
                    f"Invalid variable key: {key}. Must contain only alphanumeric characters, "
                    "underscores, and hyphens"
                )
            validate_variable_value(value, self.MAX_VARIABLE_VALUE_LENGTH)
            sanitized[key] = value
        return sanitized

    def _validate_name(self, name: Optional[str], allow_empty: bool = False) -> str:
        """Validate and normalize an environment name.

        Args:
            name: Name to validate.
            allow_empty: If True, empty strings are permitted.

        Returns:
            Sanitized name.

        Raises:
            ValidationError: If name is invalid.
        """
        if not isinstance(name, str):
            raise ValidationError("Environment name must be a string")
        name = name.strip()
        if not name and not allow_empty:
            raise ValidationError("Environment name cannot be empty or whitespace")
        if len(name) > self.MAX_NAME_LENGTH:
            raise ValidationError(
                f"Environment name too long (max {self.MAX_NAME_LENGTH} characters)"
            )
        return name

    def _validate_description(self, description: Optional[str]) -> str:
        """Validate and normalize environment description.
        
        Args:
            description: Description to validate
            
        Returns:
            Sanitized description
            
        Raises:
            ValidationError: If description is invalid
        """
        if description is None:
            return ""

        if not isinstance(description, str):
            raise ValidationError("Environment description must be a string")

        if len(description) > self.MAX_DESCRIPTION_LENGTH:
            raise ValidationError(f"Environment description too long (max {self.MAX_DESCRIPTION_LENGTH} characters)")

        return description.strip()

    def _validate_secret_keys(self, secret_keys: Optional[List[str]]) -> List[str]:
        """Validate and normalize secret_keys list.
        
        Args:
            secret_keys: List of secret key names
            
        Returns:
            Validated list of secret key names
            
        Raises:
            ValidationError: If any secret key is invalid
            SecurityError: If too many secret keys
        """
        if secret_keys is None:
            return []

        if not isinstance(secret_keys, list):
            raise ValidationError("secret_keys must be a list")

        if len(secret_keys) > self.MAX_SECRET_KEYS:
            raise SecurityError(f"Too many secret_keys (max {self.MAX_SECRET_KEYS})")

        validated: List[str] = []
        for item in secret_keys:
            if not isinstance(item, str):
                raise ValidationError("Each secret_keys entry must be a string")
            if len(item) > self.MAX_VARIABLE_KEY_LENGTH:
                raise ValidationError(f"Secret key name too long (max {self.MAX_VARIABLE_KEY_LENGTH} characters)")
            validated.append(item.strip())

        return validated

    def create_environment(
        self, name: str, variables: Dict[str, str], description: str = ""
    ) -> int:
        """Create a new environment.

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
        # Validate and normalize inputs
        name = self._validate_name(name, allow_empty=False)
        description = self._validate_description(description)
        sanitized_variables = self._validate_variables(variables)

        # Check environment count limit
        env_count = self.db.fetchone("SELECT COUNT(*) as count FROM environments")
        if env_count and env_count["count"] >= self.MAX_ENVIRONMENTS:
            raise SecurityError(f"Maximum number of environments reached ({self.MAX_ENVIRONMENTS})")

        try:
            vars_json = safe_json_dumps(sanitized_variables, max_len=200_000)
            environment_id = self.db.insert(
                "INSERT INTO environments (name, description, variables) VALUES (?, ?, ?)",
                (name, description, vars_json),
            )
            logger.info(
                "Created environment '%s' with ID %d and %d variables",
                name, environment_id, len(sanitized_variables),
            )
            return environment_id
        except DuplicateError:
            raise DuplicateError(f"Environment '{name}' already exists")
        except (SecurityError, StorageError):
            raise
        except Exception as exc:
            raise StorageError(f"Failed to create environment: {exc}") from exc

    def _decode_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """Decode JSON columns in an environment row in-place and return it.
        
        Args:
            row: Database row dict with JSON-encoded columns
            
        Returns:
            Same row dict with variables and secret_keys parsed as Python objects
        """
        row["variables"] = safe_json_loads(row.get("variables"), row_id=row.get("id"))
        secret_keys = safe_json_loads(row.get("secret_keys") or "[]", default=[], row_id=row.get("id"))
        if not isinstance(secret_keys, list):
            logger.error("Failed to parse secret_keys for environment %s", row.get("id"))
            secret_keys = []
        row["secret_keys"] = secret_keys
        return row

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
        require_positive_int(environment_id, "Environment ID")

        row = self.db.fetchone("SELECT * FROM environments WHERE id = ?", (environment_id,))
        return self._decode_row(dict(row)) if row else None

    def get_active_environment(self) -> Optional[Dict[str, Any]]:
        """Get the currently active environment"""
        row = self.db.fetchone("SELECT * FROM environments WHERE is_active = 1")
        return self._decode_row(dict(row)) if row else None

    def list_environments(self) -> List[Dict[str, Any]]:
        """
        List all environments

        Returns:
            List of environments
        """
        rows = self.db.fetchall("SELECT * FROM environments ORDER BY name")
        return [self._decode_row(row) for row in rows]

    def update_environment(
        self,
        environment_id: int,
        name: Optional[str] = None,
        variables: Optional[Dict[str, str]] = None,
        description: Optional[str] = None,
        secret_keys: Optional[List[str]] = None,
    ) -> None:
        """Update environment.

        Args:
            environment_id: Environment ID
            name: New environment name
            variables: New environment variables
            description: New environment description
            secret_keys: New list of secret key names

        Raises:
            ValidationError: If input is invalid
            SecurityError: If limits exceeded
            StorageError: If environment doesn't exist or update fails
        """
        # Validate environment_id
        require_positive_int(environment_id, "Environment ID")

        # Check environment exists
        environment = self.get_environment(environment_id)
        if not environment:
            raise StorageError(f"Environment with ID {environment_id} does not exist")

        updates: List[str] = []
        params: List[Any] = []

        # Build update clauses using validation helpers
        if name is not None:
            name = self._validate_name(name)
            updates.append("name = ?")
            params.append(name)

        if description is not None:
            description = self._validate_description(description)
            updates.append("description = ?")
            params.append(description)

        if variables is not None:
            sanitized_variables = self._validate_variables(variables)
            updates.append("variables = ?")
            try:
                params.append(safe_json_dumps(sanitized_variables, max_len=200_000))
            except SecurityError as exc:
                raise SecurityError(f"Environment variables too large: {exc}") from exc

        if secret_keys is not None:
            validated_keys = self._validate_secret_keys(secret_keys)
            updates.append("secret_keys = ?")
            try:
                params.append(safe_json_dumps(validated_keys, max_len=10_000))
            except SecurityError:
                raise SecurityError("Secret keys list too large") from None

        if not updates:
            logger.warning("No updates provided for environment %d", environment_id)
            return

        try:
            updates.append("updated_at = CURRENT_TIMESTAMP")
            params.append(environment_id)
            query = f"UPDATE environments SET {', '.join(updates)} WHERE id = ?"
            self.db.execute(query, tuple(params))
            logger.info("Updated environment '%s' (ID: %d)", environment['name'], environment_id)
        except DuplicateError:
            raise DuplicateError("Environment name already exists")
        except (SecurityError, StorageError):
            raise
        except Exception as exc:
            raise StorageError(f"Failed to update environment: {exc}") from exc

    def set_active_environment(self, environment_id: int) -> None:
        """Set active environment

        Args:
            environment_id: Environment ID to activate

        Raises:
            ValidationError: If environment_id is invalid
            StorageError: If environment doesn't exist
        """
        # Validate environment_id
        require_positive_int(environment_id, "Environment ID")

        # Check environment exists
        environment = self.get_environment(environment_id)
        if not environment:
            raise StorageError(f"Environment with ID {environment_id} does not exist")

        # Use a single UPDATE with CASE to make the switch atomic
        self.db.execute(
            "UPDATE environments SET is_active = CASE WHEN id = ? THEN 1 ELSE 0 END",
            (environment_id,),
        )
        logger.info("Activated environment '%s' (ID: %d)", environment['name'], environment_id)


    def delete_environment(self, environment_id: int) -> None:
        """Delete environment

        Args:
            environment_id: Environment ID to delete

        Raises:
            ValidationError: If environment_id is invalid
            StorageError: If environment doesn't exist or deletion fails
        """
        # Validate environment_id
        require_positive_int(environment_id, "Environment ID")

        # Check environment exists
        environment = self.get_environment(environment_id)
        if not environment:
            raise StorageError(f"Environment with ID {environment_id} does not exist")

        self.db.execute("DELETE FROM environments WHERE id = ?", (environment_id,))
        logger.warning("Deleted environment '%s' (ID: %d)", environment['name'], environment_id)


    def interpolate_variables(self, text: str, max_iterations: int = 10) -> str:
        """Replace {{variable}} placeholders with values from active environment.

        Delegates to :class:`~equinox.core.interpolation.VariableInterpolator`
        which provides cycle detection, expansion-ratio guards, and size limits.

        Args:
            text: Text with {{variable}} placeholders.
            max_iterations: Maximum interpolation passes (prevents infinite loops).

        Returns:
            Text with variables replaced.

        Raises:
            ValidationError: If input is invalid.
            SecurityError: If interpolation would cause infinite loop or excessive expansion.
        """
        if not isinstance(text, str):
            raise ValidationError("Text must be a string")

        if len(text) > self.MAX_TEXT_SIZE:
            raise SecurityError(
                f"Text too large for variable interpolation (max {self.MAX_TEXT_SIZE} bytes)"
            )

        env = self.get_active_environment()
        if not env:
            return text

        variables = env.get("variables", {})
        if not variables:
            return text

        return VariableInterpolator.interpolate(text, variables, max_iterations=max_iterations)
