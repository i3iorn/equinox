"""Environment management"""

import json
from typing import List, Dict, Any, Optional

from equinox.storage.database import Database
from equinox.core.exceptions import StorageError


class EnvironmentManager:
    """Manage environments and variables"""

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
        """
        try:
            return self.db.insert(
                "INSERT INTO environments (name, description, variables) VALUES (?, ?, ?)",
                (name, description, json.dumps(variables)),
            )
        except Exception as e:
            raise StorageError(f"Failed to create environment: {e}")

    def get_environment(self, environment_id: int) -> Optional[Dict[str, Any]]:
        """
        Get environment by ID

        Args:
            environment_id: Environment ID

        Returns:
            Environment data or None
        """
        row = self.db.fetchone("SELECT * FROM environments WHERE id = ?", (environment_id,))
        if row:
            row = dict(row)
            row["variables"] = json.loads(row["variables"])
        return row

    def get_active_environment(self) -> Optional[Dict[str, Any]]:
        """Get the currently active environment"""
        row = self.db.fetchone("SELECT * FROM environments WHERE is_active = 1")
        if row:
            row = dict(row)
            row["variables"] = json.loads(row["variables"])
        return row

    def list_environments(self) -> List[Dict[str, Any]]:
        """
        List all environments

        Returns:
            List of environments
        """
        rows = self.db.fetchall("SELECT * FROM environments ORDER BY name")
        for row in rows:
            row["variables"] = json.loads(row["variables"])
        return rows

    def update_environment(
        self,
        environment_id: int,
        name: Optional[str] = None,
        variables: Optional[Dict[str, str]] = None,
        description: Optional[str] = None,
    ) -> None:
        """Update environment"""
        updates = []
        params = []

        if name is not None:
            updates.append("name = ?")
            params.append(name)
        if description is not None:
            updates.append("description = ?")
            params.append(description)
        if variables is not None:
            updates.append("variables = ?")
            params.append(json.dumps(variables))

        if updates:
            updates.append("updated_at = CURRENT_TIMESTAMP")
            params.append(environment_id)
            query = f"UPDATE environments SET {', '.join(updates)} WHERE id = ?"
            self.db.execute(query, tuple(params))

    def set_active_environment(self, environment_id: int) -> None:
        """Set active environment"""
        # Deactivate all environments
        self.db.execute("UPDATE environments SET is_active = 0")
        # Activate specified environment
        self.db.execute("UPDATE environments SET is_active = 1 WHERE id = ?", (environment_id,))

    def delete_environment(self, environment_id: int) -> None:
        """Delete environment"""
        self.db.execute("DELETE FROM environments WHERE id = ?", (environment_id,))

    def interpolate_variables(self, text: str) -> str:
        """
        Replace {{variable}} placeholders with values from active environment

        Args:
            text: Text with {{variable}} placeholders

        Returns:
            Text with variables replaced
        """
        env = self.get_active_environment()
        if not env:
            return text

        variables = env["variables"]
        for key, value in variables.items():
            text = text.replace(f"{{{{{key}}}}}", value)

        return text
