"""Collection management"""

import json
from typing import List, Dict, Any, Optional
from datetime import datetime

from equinox.storage.database import Database
from equinox.core.request import Request
from equinox.core.exceptions import StorageError


class CollectionManager:
    """Manage request collections"""

    def __init__(self, db: Database):
        """
        Initialize collection manager

        Args:
            db: Database instance
        """
        self.db = db

    def create_collection(self, name: str, description: str = "") -> int:
        """
        Create a new collection

        Args:
            name: Collection name
            description: Collection description

        Returns:
            Collection ID
        """
        try:
            return self.db.insert(
                "INSERT INTO collections (name, description) VALUES (?, ?)", (name, description)
            )
        except Exception as e:
            raise StorageError(f"Failed to create collection: {e}")

    def get_collection(self, collection_id: int) -> Optional[Dict[str, Any]]:
        """
        Get collection by ID

        Args:
            collection_id: Collection ID

        Returns:
            Collection data or None
        """
        return self.db.fetchone("SELECT * FROM collections WHERE id = ?", (collection_id,))

    def list_collections(self) -> List[Dict[str, Any]]:
        """
        List all collections

        Returns:
            List of collections
        """
        return self.db.fetchall("SELECT * FROM collections ORDER BY name")

    def update_collection(self, collection_id: int, name: str, description: str) -> None:
        """Update collection"""
        self.db.execute(
            "UPDATE collections SET name = ?, description = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (name, description, collection_id),
        )

    def delete_collection(self, collection_id: int) -> None:
        """Delete collection and all its requests"""
        self.db.execute("DELETE FROM collections WHERE id = ?", (collection_id,))

    def save_request(self, request: Request, name: Optional[str] = None) -> int:
        """
        Save request to collection

        Args:
            request: Request object
            name: Optional request name (uses request.name if not provided)

        Returns:
            Request ID
        """
        req_name = name or request.name or f"{request.method} {request.url}"

        # Serialize auth if present
        auth_type = None
        auth_data = None
        if request.auth:
            auth_type = request.auth.to_dict().get("type")
            auth_data = json.dumps(request.auth.to_dict())

        try:
            return self.db.insert(
                """
                INSERT INTO requests
                (collection_id, name, description, method, url, headers, params, body, auth_type, auth_data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request.collection_id,
                    req_name,
                    request.description,
                    request.method,
                    request.url,
                    json.dumps(request.headers),
                    json.dumps(request.params),
                    request.body,
                    auth_type,
                    auth_data,
                ),
            )
        except Exception as e:
            raise StorageError(f"Failed to save request: {e}")

    def get_request(self, request_id: int) -> Optional[Request]:
        """
        Get request by ID

        Args:
            request_id: Request ID

        Returns:
            Request object or None
        """
        row = self.db.fetchone("SELECT * FROM requests WHERE id = ?", (request_id,))
        if not row:
            return None

        return self._row_to_request(row)

    def list_requests(self, collection_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        List requests

        Args:
            collection_id: Optional collection ID to filter by

        Returns:
            List of requests
        """
        if collection_id:
            return self.db.fetchall(
                "SELECT * FROM requests WHERE collection_id = ? ORDER BY name", (collection_id,)
            )
        return self.db.fetchall("SELECT * FROM requests ORDER BY collection_id, name")

    def delete_request(self, request_id: int) -> None:
        """Delete request"""
        self.db.execute("DELETE FROM requests WHERE id = ?", (request_id,))

    def _row_to_request(self, row: Dict[str, Any]) -> Request:
        """Convert database row to Request object"""
        from equinox.auth import BearerAuth, APIKeyAuth, BasicAuth, OAuth2Auth

        # Parse JSON fields
        headers = json.loads(row["headers"]) if row["headers"] else {}
        params = json.loads(row["params"]) if row["params"] else {}

        # Parse auth
        auth = None
        if row["auth_type"] and row["auth_data"]:
            auth_dict = json.loads(row["auth_data"])
            auth_type = auth_dict.get("type")

            if auth_type == "bearer":
                auth = BearerAuth.from_dict(auth_dict)
            elif auth_type == "api_key":
                auth = APIKeyAuth.from_dict(auth_dict)
            elif auth_type == "basic":
                auth = BasicAuth.from_dict(auth_dict)
            elif auth_type == "oauth2":
                auth = OAuth2Auth.from_dict(auth_dict)

        return Request(
            method=row["method"],
            url=row["url"],
            headers=headers,
            params=params,
            body=row["body"],
            auth=auth,
            name=row["name"],
            description=row["description"],
            collection_id=row["collection_id"],
        )
