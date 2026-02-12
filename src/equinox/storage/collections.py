"""Collection management"""

import json
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

from equinox.storage.database import Database
from equinox.core.request import Request
from equinox.core.exceptions import StorageError, ValidationError

logger = logging.getLogger(__name__)


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

        Raises:
            ValidationError: If input is invalid
            StorageError: If creation fails
        """
        # Validate inputs
        if not name or not isinstance(name, str):
            raise ValidationError("Collection name must be a non-empty string")

        if len(name) > 200:
            raise ValidationError("Collection name too long (max 200 characters)")

        if len(description) > 1000:
            raise ValidationError("Collection description too long (max 1000 characters)")

        # Sanitize name
        name = name.strip()

        if not name:
            raise ValidationError("Collection name cannot be empty or whitespace")

        try:
            collection_id = self.db.insert(
                "INSERT INTO collections (name, description) VALUES (?, ?)",
                (name, description)
            )
            logger.info(f"Created collection '{name}' with ID {collection_id}")
            return collection_id

        except Exception as e:
            # Check for unique constraint violation
            if "UNIQUE constraint failed" in str(e):
                raise StorageError(f"Collection '{name}' already exists")
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
        """Delete collection and all its requests.

        Args:
            collection_id: Collection ID to delete

        Raises:
            ValidationError: If collection_id is invalid
            StorageError: If deletion fails or collection doesn't exist
        """
        if not isinstance(collection_id, int) or collection_id <= 0:
            raise ValidationError("Collection ID must be a positive integer")

        # Check collection exists
        collection = self.get_collection(collection_id)
        if not collection:
            raise StorageError(f"Collection with ID {collection_id} does not exist")

        try:
            # Count requests that will be deleted
            requests = self.list_requests(collection_id)
            request_count = len(requests)

            self.db.execute("DELETE FROM collections WHERE id = ?", (collection_id,))

            logger.warning(
                f"Deleted collection '{collection['name']}' (ID: {collection_id}) "
                f"and {request_count} request(s)"
            )

        except Exception as e:
            raise StorageError(f"Failed to delete collection: {e}")

    def save_request(self, request: Request, collection_id: Optional[int] = None, name: Optional[str] = None) -> int:
        """
        Save request to collection

        Args:
            request: Request object
            collection_id: Collection ID (uses request.collection_id if not provided)
            name: Optional request name (uses request.name if not provided)

        Returns:
            Request ID

        Raises:
            StorageError: If save fails or collection doesn't exist
        """
        # Determine collection ID
        coll_id = collection_id if collection_id is not None else request.collection_id

        # Validate collection exists if specified
        if coll_id is not None:
            collection = self.get_collection(coll_id)
            if not collection:
                raise StorageError(f"Collection with ID {coll_id} does not exist")

        # Generate request name
        req_name = name or request.name or f"{request.method} {request.url}"

        # Truncate long names
        if len(req_name) > 200:
            req_name = req_name[:197] + "..."

        # Serialize auth if present
        auth_type = None
        auth_data = None
        if request.auth:
            try:
                auth_dict = request.auth.to_dict()
                auth_type = auth_dict.get("type")
                auth_data = json.dumps(auth_dict)
            except Exception as e:
                logger.warning(f"Failed to serialize auth: {e}")

        try:
            req_id = self.db.insert(
                """
                INSERT INTO requests
                (collection_id, name, description, method, url, headers, params, body, auth_type, auth_data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    coll_id,
                    req_name,
                    request.description or "",
                    request.method,
                    request.url,
                    json.dumps(request.headers) if request.headers else "{}",
                    json.dumps(request.params) if request.params else "{}",
                    request.body,
                    auth_type,
                    auth_data,
                ),
            )

            logger.info(f"Saved request '{req_name}' with ID {req_id} to collection {coll_id}")
            return req_id

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
        """Delete request.

        Args:
            request_id: Request ID to delete

        Raises:
            ValidationError: If request_id is invalid
            StorageError: If deletion fails or request doesn't exist
        """
        if not isinstance(request_id, int) or request_id <= 0:
            raise ValidationError("Request ID must be a positive integer")

        # Check request exists
        request = self.get_request(request_id)
        if not request:
            raise StorageError(f"Request with ID {request_id} does not exist")

        try:
            self.db.execute("DELETE FROM requests WHERE id = ?", (request_id,))
            logger.info(f"Deleted request '{request.name}' (ID: {request_id})")

        except Exception as e:
            raise StorageError(f"Failed to delete request: {e}")

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
