"""Collection management"""

import json
import logging
from typing import List, Dict, Any, Optional

from equinox.storage.database import Database
from equinox.core.request import Request
from equinox.core.exceptions import StorageError, ValidationError

logger = logging.getLogger(__name__)

_MAX_VARIABLE_KEY_LENGTH = 100
_MAX_VARIABLE_VALUE_LENGTH = 10_000


def _params_to_json(request: Request) -> str:
    """Serialise request params to JSON for storage.

    When ``params_list`` is available (rich format with enabled flags) it is
    stored as a JSON array so enable/disable state survives round-trips.
    Otherwise the plain ``params`` dict is stored for backward compatibility.
    """
    params_list = getattr(request, "params_list", None)
    if params_list:
        return json.dumps(params_list)
    return json.dumps(request.params) if request.params else "[]"


def _params_from_json(raw_str: str):
    """Deserialise params from storage, returning ``(params_dict, params_list)``.

    Handles both the legacy dict format and the new list format.
    Returns (enabled_dict, full_list_with_flags).
    """
    if not raw_str:
        return {}, []
    raw = json.loads(raw_str)
    if isinstance(raw, list):
        params_list = raw
        params = {
            entry["key"]: entry["value"]
            for entry in raw
            if entry.get("enabled", True) and entry.get("key", "").strip()
        }
    else:
        # Legacy dict — treat all as enabled
        params = raw
        params_list = [{"key": k, "value": v, "enabled": True} for k, v in raw.items()]
    return params, params_list


class CollectionManager:
    """Manage request collections."""

    MAX_NAME_LENGTH = 200
    MAX_DESCRIPTION_LENGTH = 1000
    DEFAULT_TIMEOUT = 30.0

    def __init__(self, db: Database):
        self.db = db

    def create_collection(self, name: str, description: str = "") -> int:
        """Create a new collection.

        Args:
            name: Collection name
            description: Collection description

        Returns:
            Collection ID

        Raises:
            ValidationError: If input is invalid
            StorageError: If creation fails
        """
        if not name or not isinstance(name, str):
            raise ValidationError("Collection name must be a non-empty string")
        if len(name) > self.MAX_NAME_LENGTH:
            raise ValidationError(f"Collection name too long (max {self.MAX_NAME_LENGTH} characters)")
        if len(description) > self.MAX_DESCRIPTION_LENGTH:
            raise ValidationError(f"Collection description too long (max {self.MAX_DESCRIPTION_LENGTH} characters)")

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
        except Exception as exc:
            if "UNIQUE constraint failed" in str(exc):
                raise StorageError(f"Collection '{name}' already exists")
            raise StorageError(f"Failed to create collection: {exc}")

    def get_collection(self, collection_id: int) -> Optional[Dict[str, Any]]:
        """Get collection by ID."""
        return self.db.fetchone("SELECT * FROM collections WHERE id = ?", (collection_id,))

    def list_collections(self) -> List[Dict[str, Any]]:
        """List all collections."""
        return self.db.fetchall("SELECT * FROM collections ORDER BY name")

    def update_collection(self, collection_id: int, name: str, description: str) -> None:
        """Update collection name and description."""
        self.db.execute(
            "UPDATE collections SET name = ?, description = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (name, description, collection_id),
        )

    def rename_collection(self, collection_id: int, new_name: str) -> None:
        """Rename a collection.

        Args:
            collection_id: Collection ID
            new_name: New name

        Raises:
            ValidationError: If new_name is invalid
            StorageError: If collection not found or name taken
        """
        new_name = new_name.strip()
        if not new_name:
            raise ValidationError("Collection name cannot be empty")
        if len(new_name) > self.MAX_NAME_LENGTH:
            raise ValidationError(f"Collection name too long (max {self.MAX_NAME_LENGTH} characters)")
        if not self.get_collection(collection_id):
            raise StorageError(f"Collection {collection_id} not found")
        try:
            self.db.execute(
                "UPDATE collections SET name = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (new_name, collection_id),
            )
            logger.info(f"Renamed collection {collection_id} to '{new_name}'")
        except Exception as exc:
            if "UNIQUE constraint" in str(exc):
                raise StorageError(f"A collection named '{new_name}' already exists")
            raise StorageError(f"Failed to rename collection: {exc}")

    def rename_request(self, request_id: int, new_name: str) -> None:
        """Rename a saved request.

        Args:
            request_id: Request ID
            new_name: New name

        Raises:
            ValidationError: If new_name is invalid
            StorageError: If request not found
        """
        new_name = new_name.strip()
        if not new_name:
            raise ValidationError("Request name cannot be empty")
        if len(new_name) > self.MAX_NAME_LENGTH:
            raise ValidationError(f"Request name too long (max {self.MAX_NAME_LENGTH} characters)")
        if not self.get_request(request_id):
            raise StorageError(f"Request {request_id} not found")
        try:
            self.db.execute(
                "UPDATE requests SET name = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (new_name, request_id),
            )
            logger.info(f"Renamed request {request_id} to '{new_name}'")
        except Exception as exc:
            raise StorageError(f"Failed to rename request: {exc}")

    def duplicate_request(self, request_id: int, new_name: Optional[str] = None) -> int:
        """Duplicate a saved request within the same collection.

        Args:
            request_id: Source request ID
            new_name: Name for the copy (defaults to "Copy of <original name>")

        Returns:
            New request ID

        Raises:
            StorageError: If source request not found
        """
        row = self.db.fetchone("SELECT * FROM requests WHERE id = ?", (request_id,))
        if not row:
            raise StorageError(f"Request {request_id} not found")
        row = dict(row)
        copy_name = new_name or f"Copy of {row['name']}"
        if len(copy_name) > self.MAX_NAME_LENGTH:
            copy_name = copy_name[:self.MAX_NAME_LENGTH - 3] + "..."
        try:
            new_id = self.db.insert(
                """
                INSERT INTO requests
                (collection_id, name, description, method, url, headers, params, body,
                 auth_type, auth_data, captures, pre_script, post_script,
                 cert_path, cert_key_path, folder, timeout, verify_ssl, follow_redirects,
                 path_params)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["collection_id"], copy_name, row.get("description", ""),
                    row["method"], row["url"],
                    row.get("headers", "{}"), row.get("params", "{}"),
                    row.get("body"), row.get("auth_type"), row.get("auth_data"),
                    row.get("captures", "[]"),
                    row.get("pre_script", ""), row.get("post_script", ""),
                    row.get("cert_path"), row.get("cert_key_path"),
                    row.get("folder") or None,
                    row.get("timeout") or self.DEFAULT_TIMEOUT,
                    row.get("verify_ssl", 1),
                    row.get("follow_redirects", 1),
                    row.get("path_params", "{}"),
                ),
            )
            logger.info(f"Duplicated request {request_id} → {new_id} ('{copy_name}')")
            return new_id
        except Exception as exc:
            raise StorageError(f"Failed to duplicate request: {exc}")

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

        collection = self.get_collection(collection_id)
        if not collection:
            raise StorageError(f"Collection with ID {collection_id} does not exist")

        try:
            request_count = len(self.list_requests(collection_id))
            self.db.execute("DELETE FROM collections WHERE id = ?", (collection_id,))
            logger.warning(
                f"Deleted collection '{collection['name']}' (ID: {collection_id}) "
                f"and {request_count} request(s)"
            )
        except Exception as exc:
            raise StorageError(f"Failed to delete collection: {exc}")

    def save_request(self, request: Request, collection_id: Optional[int] = None, name: Optional[str] = None) -> int:
        """Save request to collection.

        Args:
            request: Request object
            collection_id: Collection ID (uses request.collection_id if not provided)
            name: Optional request name (uses request.name if not provided)

        Returns:
            Request ID

        Raises:
            StorageError: If save fails or collection doesn't exist
        """
        coll_id = collection_id if collection_id is not None else request.collection_id
        if coll_id is not None and not self.get_collection(coll_id):
            raise StorageError(f"Collection with ID {coll_id} does not exist")

        req_name = self._resolve_request_name(name or request.name or f"{request.method} {request.url}")
        auth_type, auth_data = self._serialize_auth(request.auth)
        captures_json   = json.dumps(request.captures)   if request.captures   else "[]"
        assertions_json = json.dumps(request.assertions) if request.assertions else "[]"

        try:
            req_id = self.db.insert(
                """
                INSERT INTO requests
                (collection_id, name, description, method, url, headers, params, body,
                 auth_type, auth_data, captures, assertions, pre_script, post_script,
                 cert_path, cert_key_path, folder, timeout, verify_ssl, follow_redirects)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    coll_id,
                    req_name,
                    request.description or "",
                    request.method,
                    request.url,
                    json.dumps(request.headers) if request.headers else "{}",
                    _params_to_json(request),
                    request.body,
                    auth_type,
                    auth_data,
                    captures_json,
                    assertions_json,
                    request.pre_script or "",
                    request.post_script or "",
                    request.cert_path,
                    request.cert_key_path,
                    request.folder or None,
                    request.timeout,
                    int(request.verify_ssl),
                    int(request.follow_redirects),
                ),
            )
            logger.info(f"Saved request '{req_name}' with ID {req_id} to collection {coll_id}")
            return req_id
        except Exception as exc:
            raise StorageError(f"Failed to save request: {exc}")

    def _resolve_request_name(self, raw_name: str) -> str:
        """Validate and normalise a request name for storage.

        Raises:
            ValidationError: If the resulting name is empty.
        """
        name = raw_name.strip()
        if not name:
            raise ValidationError("Request name cannot be empty or whitespace")
        if len(name) > self.MAX_NAME_LENGTH:
            name = name[:self.MAX_NAME_LENGTH - 3] + "..."
        return name

    def update_request_auth(self, request_id: int, auth) -> None:
        """Update only the auth on an existing request.

        Args:
            request_id: ID of the request to update.
            auth: An auth strategy object (with ``.to_dict()``), or ``None``
                  to clear auth.

        Raises:
            StorageError: If the request does not exist or the update fails.
        """
        if not self.db.fetchone("SELECT id FROM requests WHERE id = ?", (request_id,)):
            raise StorageError(f"Request {request_id} not found")

        auth_type, auth_data = self._serialize_auth(auth)
        try:
            self.db.execute(
                "UPDATE requests SET auth_type = ?, auth_data = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (auth_type, auth_data, request_id),
            )
            logger.info(f"Updated auth on request {request_id}")
        except Exception as exc:
            raise StorageError(f"Failed to update request auth: {exc}")

    def get_request(self, request_id: int) -> Optional[Request]:
        """Get request by ID."""
        row = self.db.fetchone("SELECT * FROM requests WHERE id = ?", (request_id,))
        if not row:
            return None
        return self._row_to_request(row)

    def list_requests(self, collection_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """List requests, optionally filtered by collection."""
        if collection_id:
            return self.db.fetchall(
                "SELECT * FROM requests WHERE collection_id = ? ORDER BY sort_order, name",
                (collection_id,),
            )
        return self.db.fetchall(
            "SELECT * FROM requests ORDER BY collection_id, sort_order, name"
        )

    def list_requests_in_collection(self, collection_id: int) -> List[Dict[str, Any]]:
        """Alias for list_requests(collection_id) — explicit name used by exporters."""
        return self.list_requests(collection_id)

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

        request = self.get_request(request_id)
        if not request:
            raise StorageError(f"Request with ID {request_id} does not exist")

        try:
            self.db.execute("DELETE FROM requests WHERE id = ?", (request_id,))
            logger.info(f"Deleted request '{request.name}' (ID: {request_id})")
        except Exception as exc:
            raise StorageError(f"Failed to delete request: {exc}")

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
        if not isinstance(collection_id, int) or collection_id <= 0:
            raise ValidationError("Collection ID must be a positive integer")
        if not self.get_collection(collection_id):
            raise StorageError(f"Collection with ID {collection_id} does not exist")
        if not key or not isinstance(key, str):
            raise ValidationError("Variable key must be a non-empty string")
        if len(key) > _MAX_VARIABLE_KEY_LENGTH:
            raise ValidationError(f"Variable key too long (max {_MAX_VARIABLE_KEY_LENGTH} characters)")

        key = key.strip()
        if not key:
            raise ValidationError("Variable key cannot be empty or whitespace")

        if not isinstance(value, str):
            raise ValidationError("Variable value must be a string")
        if len(value) > _MAX_VARIABLE_VALUE_LENGTH:
            raise ValidationError(f"Variable value too long (max {_MAX_VARIABLE_VALUE_LENGTH} characters)")
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
        if not isinstance(collection_id, int) or collection_id <= 0:
            raise ValidationError("Collection ID must be a positive integer")
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
        if not isinstance(collection_id, int) or collection_id <= 0:
            raise ValidationError("Collection ID must be a positive integer")
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
        if not isinstance(collection_id, int) or collection_id <= 0:
            raise ValidationError("Collection ID must be a positive integer")
        if not isinstance(group_id, int) or group_id <= 0:
            raise ValidationError("Variable group ID must be a positive integer")
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
        if not isinstance(collection_id, int) or collection_id <= 0:
            raise ValidationError("Collection ID must be a positive integer")
        if not isinstance(group_id, int) or group_id <= 0:
            raise ValidationError("Variable group ID must be a positive integer")

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
        if not isinstance(collection_id, int) or collection_id <= 0:
            raise ValidationError("Collection ID must be a positive integer")
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
        if not isinstance(collection_id, int) or collection_id <= 0:
            raise ValidationError("Collection ID must be a positive integer")

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

    # ── Hierarchical auth ─────────────────────────────────────────────

    @staticmethod
    def _serialize_auth(auth) -> "tuple[Optional[str], Optional[str]]":
        """Return (auth_type, auth_data_json) from an auth strategy object or None."""
        if auth is None:
            return None, None
        try:
            d = auth.to_dict()
            return d.get("type"), json.dumps(d)
        except Exception:
            return None, None

    @staticmethod
    def _deserialize_auth(auth_type: "Optional[str]", auth_data: "Optional[str]"):
        """Return an auth strategy object from DB columns, or None."""
        if not auth_type or not auth_data:
            return None
        from equinox.auth import BearerAuth, APIKeyAuth, BasicAuth, OAuth2Auth
        try:
            d = json.loads(auth_data)
        except (ValueError, TypeError):
            return None
        t = d.get("type", auth_type)
        if t == "bearer":
            return BearerAuth.from_dict(d)
        if t == "api_key":
            return APIKeyAuth.from_dict(d)
        if t == "basic":
            return BasicAuth.from_dict(d)
        if t == "oauth2":
            return OAuth2Auth.from_dict(d)
        if t == "aws_sigv4":
            try:
                from equinox.auth.aws_sigv4 import AWSSigV4Auth
                return AWSSigV4Auth.from_dict(d)
            except Exception:
                pass
        return None

    # ── Collection-level auth ─────────────────────────────────────────

    def set_collection_auth(self, collection_id: int, auth) -> None:
        """Set or clear the default auth for a collection.

        Args:
            collection_id: Collection ID.
            auth: An auth strategy object, or ``None`` to clear.
        """
        a_type, a_data = self._serialize_auth(auth)
        self.db.execute(
            "UPDATE collections SET auth_type=?, auth_data=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (a_type, a_data, collection_id),
        )
        logger.info("Set auth on collection %d: %s", collection_id, a_type)

    def get_collection_auth(self, collection_id: int):
        """Return the auth strategy set on a collection, or ``None``."""
        row = self.db.fetchone(
            "SELECT auth_type, auth_data FROM collections WHERE id=?",
            (collection_id,),
        )
        if not row:
            return None
        return self._deserialize_auth(row["auth_type"], row["auth_data"])

    # ── Folder-level auth ─────────────────────────────────────────────

    def set_folder_auth(self, collection_id: int, folder_path: str, auth) -> None:
        """Set or clear the default auth for a folder.

        The folder must already exist in ``collection_folders``.

        Args:
            collection_id: Collection ID.
            folder_path: Folder path (e.g. ``"Auth/OAuth"``).
            auth: An auth strategy object, or ``None`` to clear.
        """
        a_type, a_data = self._serialize_auth(auth)
        self.db.execute(
            "UPDATE collection_folders SET auth_type=?, auth_data=? "
            "WHERE collection_id=? AND path=?",
            (a_type, a_data, collection_id, folder_path),
        )
        logger.info(
            "Set auth on folder %r in collection %d: %s",
            folder_path, collection_id, a_type,
        )

    def get_folder_auth(self, collection_id: int, folder_path: str):
        """Return the auth strategy set on a folder, or ``None``."""
        row = self.db.fetchone(
            "SELECT auth_type, auth_data FROM collection_folders "
            "WHERE collection_id=? AND path=?",
            (collection_id, folder_path),
        )
        if not row:
            return None
        return self._deserialize_auth(row["auth_type"], row["auth_data"])

    # ── Resolve effective auth (request → folders → collection) ───────

    def resolve_effective_auth(self, request: "Request"):
        """Walk the auth hierarchy and return the first auth found.

        Resolution order:
        1. The request's own ``auth`` (from its ``auth_type``/``auth_data`` columns).
        2. The request's folder auth, then each parent folder up to root.
        3. The collection's auth.

        Returns:
            A 2-tuple ``(auth_object, source_label)`` where *source_label*
            is ``"request"``, ``"folder:<path>"``, or ``"collection"``
            (or ``(None, None)`` if nothing is configured).
        """
        if request.auth is not None:
            return request.auth, "request"

        collection_id = request.collection_id
        if not collection_id:
            return None, None

        folder = request.folder
        if folder:
            parts = folder.split("/")
            for depth in range(len(parts), 0, -1):
                ancestor = "/".join(parts[:depth])
                auth = self.get_folder_auth(collection_id, ancestor)
                if auth is not None:
                    return auth, f"folder:{ancestor}"

        auth = self.get_collection_auth(collection_id)
        if auth is not None:
            return auth, "collection"

        return None, None

    def _row_to_request(self, row: Dict[str, Any]) -> Request:
        """Convert a database row to a Request object."""
        headers = json.loads(row["headers"]) if row["headers"] else {}
        params, params_list = _params_from_json(row.get("params", ""))
        auth = self._deserialize_auth(row.get("auth_type"), row.get("auth_data"))

        try:
            captures = json.loads(row["captures"]) if row.get("captures") else []
            if not isinstance(captures, list):
                captures = []
        except (ValueError, TypeError):
            captures = []

        try:
            assertions = json.loads(row["assertions"]) if row.get("assertions") else []
            if not isinstance(assertions, list):
                assertions = []
        except (ValueError, TypeError):
            assertions = []

        return Request(
            method=row["method"],
            url=row["url"],
            headers=headers,
            params=params,
            params_list=params_list,
            body=row["body"],
            auth=auth,
            timeout=float(row.get("timeout") or self.DEFAULT_TIMEOUT),
            verify_ssl=bool(row.get("verify_ssl", 1)),
            follow_redirects=bool(row.get("follow_redirects", 1)),
            name=row["name"],
            description=row["description"],
            collection_id=row["collection_id"],
            folder=row.get("folder") or None,
            id=row["id"],
            captures=captures,
            assertions=assertions,
            pre_script=row.get("pre_script") or "",
            post_script=row.get("post_script") or "",
            cert_path=row.get("cert_path") or None,
            cert_key_path=row.get("cert_key_path") or None,
        )

    def update_request(self, request: "Request") -> None:
        """Persist in-place edits to an existing saved request.

        This is used by the autosave mechanism: when a user loads a collection
        request, edits it, then navigates away, the panel calls this method to
        keep the DB in sync without a separate "Save" action.

        Args:
            request: A :class:`Request` object whose ``id`` field is set
                (i.e. it was previously returned by :meth:`get_request`).

        Raises:
            ValidationError: If ``request.id`` is not set.
            StorageError: If the request is not found or the update fails.
        """
        if not getattr(request, "id", None):
            raise ValidationError("Cannot update request without an ID")

        auth_type, auth_data = self._serialize_auth(request.auth)
        captures_json   = json.dumps(request.captures)   if request.captures   else "[]"
        assertions_json = json.dumps(request.assertions) if request.assertions else "[]"

        try:
            self.db.execute(
                """
                UPDATE requests SET
                    method=?, url=?, headers=?, params=?, body=?,
                    auth_type=?, auth_data=?,
                    timeout=?, verify_ssl=?, follow_redirects=?,
                    folder=?,
                    captures=?, assertions=?, pre_script=?, post_script=?,
                    cert_path=?, cert_key_path=?,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (
                    request.method,
                    request.url,
                    json.dumps(request.headers) if request.headers else "{}",
                    _params_to_json(request),
                    request.body,
                    auth_type,
                    auth_data,
                    request.timeout,
                    int(request.verify_ssl),
                    int(request.follow_redirects),
                    request.folder or None,
                    captures_json,
                    assertions_json,
                    request.pre_script or "",
                    request.post_script or "",
                    request.cert_path,
                    request.cert_key_path,
                    request.id,
                ),
            )
            logger.info("Auto-saved request %d (%r)", request.id, request.name)
        except Exception as exc:
            raise StorageError(f"Failed to update request: {exc}") from exc

    def move_request_to_folder(self, request_id: int, folder: Optional[str]) -> None:
        """Move a request to a different folder (or root if folder is None/empty).

        Args:
            request_id: Request ID to move.
            folder: Destination folder path (e.g. "Auth/OAuth"), or None/empty for root.
        """
        self.db.execute(
            "UPDATE requests SET folder=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (folder or None, request_id),
        )
        logger.info("Moved request %d to folder %r", request_id, folder)

    def move_request_to_collection(
        self,
        request_id: int,
        target_collection_id: int,
        folder: Optional[str] = None,
    ) -> None:
        """Move a request to a different collection (and optionally folder).

        Args:
            request_id: Request ID to move.
            target_collection_id: Destination collection ID.
            folder: Destination folder path, or ``None`` for collection root.

        Raises:
            StorageError: If the request or target collection does not exist.
        """
        if not self.db.fetchone("SELECT id FROM requests WHERE id = ?", (request_id,)):
            raise StorageError(f"Request {request_id} not found")
        if not self.get_collection(target_collection_id):
            raise StorageError(f"Target collection {target_collection_id} does not exist")
        self.db.execute(
            "UPDATE requests SET collection_id=?, folder=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (target_collection_id, folder or None, request_id),
        )
        logger.info(
            "Moved request %d to collection %d, folder %r",
            request_id, target_collection_id, folder,
        )

    # ── Reordering helpers ────────────────────────────────────────────

    def set_sort_order(self, request_id: int, sort_order: int) -> None:
        """Set the sort_order for a single request."""
        self.db.execute(
            "UPDATE requests SET sort_order=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (sort_order, request_id),
        )

    def reorder_requests(self, ordered_ids: List[int]) -> None:
        """Bulk-update sort_order for a list of request IDs.

        The first ID gets sort_order=0, the second gets 1, etc.

        Args:
            ordered_ids: Request IDs in the desired display order.
        """
        for position, req_id in enumerate(ordered_ids):
            self.db.execute(
                "UPDATE requests SET sort_order=? WHERE id=?",
                (position, req_id),
            )
        logger.info("Reordered %d requests", len(ordered_ids))

    def sort_requests_alphabetically(
        self, collection_id: int, folder: Optional[str] = None,
    ) -> None:
        """Sort requests A→Z by name within a collection+folder group.

        Args:
            collection_id: Collection to sort inside.
            folder: Folder path, or ``None`` for collection root.
        """
        rows = self._select_group(collection_id, folder)
        sorted_rows = sorted(rows, key=lambda r: (r["name"] or "").lower())
        for position, row in enumerate(sorted_rows):
            self.db.execute(
                "UPDATE requests SET sort_order=? WHERE id=?",
                (position, row["id"]),
            )
        logger.info(
            "Sorted %d request(s) alphabetically in collection %d, folder %r",
            len(sorted_rows), collection_id, folder,
        )

    def sort_requests_by_method(
        self, collection_id: int, folder: Optional[str] = None,
    ) -> None:
        """Sort requests by HTTP method then name within a collection+folder group.

        Method order: GET, POST, PUT, PATCH, DELETE, HEAD, OPTIONS, then others.

        Args:
            collection_id: Collection to sort inside.
            folder: Folder path, or ``None`` for collection root.
        """
        method_rank = {
            "GET": 0, "POST": 1, "PUT": 2, "PATCH": 3,
            "DELETE": 4, "HEAD": 5, "OPTIONS": 6,
        }
        rows = self._select_group(collection_id, folder)
        sorted_rows = sorted(
            rows,
            key=lambda r: (
                method_rank.get(r["method"], 99),
                (r["name"] or "").lower(),
            ),
        )
        for position, row in enumerate(sorted_rows):
            self.db.execute(
                "UPDATE requests SET sort_order=? WHERE id=?",
                (position, row["id"]),
            )
        logger.info(
            "Sorted %d request(s) by method in collection %d, folder %r",
            len(sorted_rows), collection_id, folder,
        )

    def _select_group(
        self, collection_id: int, folder: Optional[str],
    ) -> List[Dict[str, Any]]:
        """Return requests in a collection+folder group."""
        if folder:
            return self.db.fetchall(
                "SELECT id, name, method FROM requests "
                "WHERE collection_id=? AND folder=? ORDER BY sort_order, name",
                (collection_id, folder),
            )
        return self.db.fetchall(
            "SELECT id, name, method FROM requests "
            "WHERE collection_id=? AND (folder IS NULL OR folder='') "
            "ORDER BY sort_order, name",
            (collection_id,),
        )

    def create_folder(
        self,
        collection_id: int,
        path: str,
        description: str = "",
    ) -> None:
        """Create an explicit folder record so the folder persists even when empty.

        Idempotent — silently succeeds if the folder already exists (INSERT OR IGNORE).

        Args:
            collection_id: Collection to create the folder in.
            path: Folder path (e.g. ``"Auth"`` or ``"Auth/OAuth"``).
                  Must be non-empty, must not start or end with ``/``,
                  and must not contain empty segments (``//``).
            description: Optional human description.

        Raises:
            ValidationError: If *collection_id* or *path* is invalid.
            StorageError: If the collection does not exist or the DB write fails.
        """
        if not isinstance(collection_id, int) or collection_id <= 0:
            raise ValidationError("collection_id must be a positive integer")
        path = (path or "").strip()
        if not path:
            raise ValidationError("Folder path must not be empty")
        if path.startswith("/") or path.endswith("/"):
            raise ValidationError("Folder path must not start or end with '/'")
        if "//" in path:
            raise ValidationError("Folder path must not contain empty segments ('//')")
        if not self.get_collection(collection_id):
            raise StorageError(f"Collection {collection_id} does not exist")
        try:
            self.db.execute(
                "INSERT OR IGNORE INTO collection_folders "
                "(collection_id, path, description) VALUES (?, ?, ?)",
                (collection_id, path, description or ""),
            )
            logger.info("Created folder %r in collection %d", path, collection_id)
        except Exception as exc:
            raise StorageError(f"Failed to create folder: {exc}") from exc

    def list_folders(self, collection_id: int) -> List[str]:
        """Return all explicit folder paths for *collection_id*, sorted alphabetically."""
        rows = self.db.fetchall(
            "SELECT path FROM collection_folders WHERE collection_id=? ORDER BY path",
            (collection_id,),
        )
        return [row["path"] for row in rows]

    def rename_folder(self, collection_id: int, old_path: str, new_path: str) -> None:
        """Rename a folder and all sub-folders within a collection.

        All requests whose folder equals *old_path* or starts with
        ``old_path/`` have the leading prefix replaced by *new_path*.

        Args:
            collection_id: Collection to operate on.
            old_path: Current folder path (e.g. "Auth").
            new_path: New folder path (e.g. "Authentication").
        """
        rows = self.db.fetchall(
            "SELECT id, folder FROM requests WHERE collection_id=? AND ("
            "folder=? OR folder LIKE ?)",
            (collection_id, old_path, f"{old_path}/%"),
        )
        for row in rows:
            current = row["folder"] or ""
            if current == old_path:
                updated = new_path
            elif current.startswith(f"{old_path}/"):
                updated = new_path + current[len(old_path):]
            else:
                continue
            self.db.execute(
                "UPDATE requests SET folder=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (updated or None, row["id"]),
            )
        # Keep collection_folders records in sync
        folder_rows = self.db.fetchall(
            "SELECT id, path FROM collection_folders "
            "WHERE collection_id=? AND (path=? OR path LIKE ?)",
            (collection_id, old_path, f"{old_path}/%"),
        )
        for row in folder_rows:
            current = row["path"]
            if current == old_path:
                updated = new_path
            elif current.startswith(f"{old_path}/"):
                updated = new_path + current[len(old_path):]
            else:
                continue
            self.db.execute(
                "UPDATE collection_folders SET path=? WHERE id=?",
                (updated, row["id"]),
            )
        logger.info(
            "Renamed folder %r → %r in collection %d (%d request(s), %d folder record(s) updated)",
            old_path, new_path, collection_id, len(rows), len(folder_rows),
        )

    def delete_folder(
        self,
        collection_id: int,
        folder_path: str,
        move_to_root: bool = True,
    ) -> None:
        """Delete a folder within a collection.

        Args:
            collection_id: Collection to operate on.
            folder_path: Folder path to delete (e.g. "Auth/OAuth").
            move_to_root: If True (default), move all requests in the folder
                (and sub-folders) to the collection root.  If False, delete
                those requests entirely.
        """
        rows = self.db.fetchall(
            "SELECT id FROM requests WHERE collection_id=? AND ("
            "folder=? OR folder LIKE ?)",
            (collection_id, folder_path, f"{folder_path}/%"),
        )
        request_ids = [row["id"] for row in rows]
        if request_ids:
            if move_to_root:
                for req_id in request_ids:
                    self.db.execute(
                        "UPDATE requests SET folder=NULL, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (req_id,),
                    )
                logger.info(
                    "Moved %d request(s) from folder %r to root in collection %d",
                    len(request_ids), folder_path, collection_id,
                )
            else:
                for req_id in request_ids:
                    self.db.execute("DELETE FROM requests WHERE id=?", (req_id,))
                logger.info(
                    "Deleted %d request(s) in folder %r from collection %d",
                    len(request_ids), folder_path, collection_id,
                )
        # Always clean up explicit folder records (handles empty folders too)
        self.db.execute(
            "DELETE FROM collection_folders WHERE collection_id=? AND (path=? OR path LIKE ?)",
            (collection_id, folder_path, f"{folder_path}/%"),
        )
