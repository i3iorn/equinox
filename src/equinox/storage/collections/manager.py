"""Collection management"""

import logging
from typing import Any

from equinox.core.exceptions import DuplicateError, StorageError, ValidationError
from equinox.core.request import Request
from equinox.storage.collections.auth import CollectionAuthMixin
from equinox.storage.collections.folders import CollectionFoldersMixin
from equinox.storage.collections.ordering import CollectionOrderingMixin
from equinox.storage.collections.variables import CollectionVariablesMixin
from equinox.storage.database import Database
from equinox.storage.utils import (
    MAX_DESCRIPTION_LENGTH as _MAX_DESC,
)
from equinox.storage.utils import (
    MAX_NAME_LENGTH as _MAX_NAME,
)
from equinox.storage.utils import (
    require_positive_int,
    require_str,
    safe_json_dumps,
    safe_json_loads,
)

logger = logging.getLogger(__name__)


def _params_to_json(request: Request) -> str:
    """Serialise request params to JSON for storage.

    When ``params_list`` is available (rich format with enabled flags) it is
    stored as a JSON array so enable/disable state survives round-trips.
    Otherwise the plain ``params`` dict is stored for backward compatibility.
    """
    params_list = request.params_list
    if params_list:
        return safe_json_dumps(params_list, max_len=50_000)
    return safe_json_dumps(request.params or {}, max_len=50_000) if request.params else "[]"


def _params_from_json(raw_str: str):
    """Deserialise params from storage, returning ``(params_dict, params_list)``.

    Handles both the legacy dict format and the new list format.
    Returns (enabled_dict, full_list_with_flags).
    """
    if not raw_str:
        return {}, []
    raw = safe_json_loads(raw_str)
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


class CollectionManager(
    CollectionVariablesMixin,
    CollectionAuthMixin,
    CollectionFoldersMixin,
    CollectionOrderingMixin,
):
    """Manage request collections."""

    MAX_NAME_LENGTH = _MAX_NAME
    MAX_DESCRIPTION_LENGTH = _MAX_DESC
    DEFAULT_TIMEOUT = 30.0

    # ── Private validation helpers ──────────────────────────────────────

    def _validate_name(self, name: str, label: str = "name", *, required: bool = True) -> str:
        """Validate and strip a name field using the shared ``require_str`` utility.

        Args:
            name:     The raw name value to validate.
            label:    Human-readable field name for error messages.
            required: When True, empty/whitespace-only strings are rejected.

        Returns:
            Stripped, valid name string.

        Raises:
            ValidationError: If validation fails.
        """
        return require_str(name, label, self.MAX_NAME_LENGTH, required=required)

    def _validate_description(self, description: str, label: str = "description") -> str:
        """Validate and strip a description field (not required)."""
        return require_str(description, label, self.MAX_DESCRIPTION_LENGTH, required=False)

    # Canonical INSERT for the requests table — single source of truth used by
    # save_request, duplicate_request, insert_request_row, and importers.
    REQUEST_INSERT_SQL = """
        INSERT INTO requests
        (collection_id, name, description, method, url, headers, params, body,
         auth_type, auth_data, captures, assertions, pre_script, post_script,
         cert_path, cert_key_path, folder, timeout, verify_ssl, follow_redirects,
         path_params)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    def __init__(self, db: Database):
        self.db = db

    # Local helpers to centralise JSON serialization/deserialization logic
    @staticmethod
    def _serialize_json_field(obj: Any, *, max_len: int, default: str = "{}") -> str:
        if not obj:
            return default
        return safe_json_dumps(obj, max_len=max_len)

    @staticmethod
    def _serialize_list_field(obj: Any, *, max_len: int) -> str:
        if not obj:
            return "[]"
        return safe_json_dumps(obj, max_len=max_len)

    # ── Centralised request INSERT helpers ─────────────────────────────

    def _build_request_insert_params(
        self,
        request: Request,
        collection_id: int,
        *,
        name_override: str | None = None,
    ) -> tuple:
        """Build the 21-element parameter tuple for :attr:`REQUEST_INSERT_SQL`.

        Args:
            request: The Request object to persist.
            collection_id: Target collection ID.
            name_override: If set, used instead of ``request.name``.

        Returns:
            A tuple matching the ``?`` placeholders in :attr:`REQUEST_INSERT_SQL`.
        """
        raw_name = name_override or request.name or f"{request.method} {request.url}"
        req_name = self._resolve_request_name(raw_name)
        auth_type, auth_data = self._serialize_auth(request.auth)
        captures_json = self._serialize_list_field(request.captures, max_len=50_000)
        assertions_json = self._serialize_list_field(request.assertions, max_len=50_000)
        headers_json = self._serialize_json_field(
            request.headers.as_canonical_dict(lowercase=False)
            if hasattr(request.headers, "as_canonical_dict")
            else (request.headers or {}),
            max_len=100_000,
            default="{}",
        )
        return (
            collection_id,
            req_name,
            request.description or "",
            request.method,
            request.url,
            headers_json,
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
            self._serialize_json_field(request.path_params, max_len=50_000, default="{}"),
        )

    def insert_request_row(
        self,
        request: Request,
        collection_id: int,
        *,
        name_override: str | None = None,
        tx: Any | None = None,
    ) -> int:
        """Insert a request row using the canonical 21-column INSERT.

        This is the **single** place that maps a :class:`Request` to a DB row.
        Both :meth:`save_request` and external importers should call this
        instead of duplicating the SQL.

        Args:
            request: Request object to persist.
            collection_id: Target collection ID.
            name_override: Override the request's name for storage.
            tx: Optional transaction helper from :meth:`Database.transaction`.
                When provided the INSERT runs inside the caller's transaction.

        Returns:
            New request row ID.
        """
        params = self._build_request_insert_params(
            request,
            collection_id,
            name_override=name_override,
        )
        inserter = tx if tx is not None else self.db
        return inserter.insert(self.REQUEST_INSERT_SQL, params)

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
        name = self._validate_name(name, "Collection name")
        description = self._validate_description(description, "Collection description")

        try:
            collection_id = self.db.insert(
                "INSERT INTO collections (name, description) VALUES (?, ?)", (name, description)
            )
            logger.info("Created collection %r with ID %d", name, collection_id)
            return collection_id
        except DuplicateError:
            # Database layer raises DuplicateError for unique constraint
            raise StorageError(f"Collection '{name}' already exists")
        except Exception as exc:
            raise StorageError(f"Failed to create collection: {exc}")

    def get_collection(self, collection_id: int) -> dict[str, Any] | None:
        """Get collection by ID."""
        require_positive_int(collection_id, "Collection ID")
        result = self.db.fetchone("SELECT * FROM collections WHERE id = ?", (collection_id,))
        if result:
            logger.debug("Retrieved collection id=%d name=%s", collection_id, result.get("name"))
        return result

    def list_collections(self) -> list[dict[str, Any]]:
        """List all collections."""
        return self.db.fetchall("SELECT * FROM collections ORDER BY name")

    def update_collection(self, collection_id: int, name: str, description: str) -> None:
        """Update collection name and description.

        Args:
            collection_id: Collection ID
            name: New name
            description: New description

        Raises:
            ValidationError: If input is invalid
            StorageError: If collection not found or name taken
        """
        require_positive_int(collection_id, "Collection ID")
        name = self._validate_name(name, "Collection name")
        description = self._validate_description(description, "Collection description")

        if not self.get_collection(collection_id):
            raise StorageError(f"Collection with ID {collection_id} does not exist")

        try:
            self.db.execute(
                "UPDATE collections SET name = ?, description = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (name, description, collection_id),
            )
            logger.info("Updated collection id=%d: name=%s", collection_id, name)
        except DuplicateError:
            raise StorageError(f"A collection named '{name}' already exists")
        except Exception as exc:
            raise StorageError(f"Failed to update collection: {exc}")

    def rename_collection(self, collection_id: int, new_name: str) -> None:
        """Rename a collection.

        Args:
            collection_id: Collection ID
            new_name: New name

        Raises:
            ValidationError: If new_name is invalid
            StorageError: If collection not found or name taken
        """
        require_positive_int(collection_id, "Collection ID")
        new_name = self._validate_name(new_name, "Collection name")
        if not self.get_collection(collection_id):
            raise StorageError(f"Collection {collection_id} not found")
        try:
            self.db.execute(
                "UPDATE collections SET name = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (new_name, collection_id),
            )
            logger.info("Renamed collection %d to %r", collection_id, new_name)
        except DuplicateError:
            raise StorageError(f"A collection named '{new_name}' already exists")
        except Exception as exc:
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
        require_positive_int(request_id, "Request ID")
        new_name = self._validate_name(new_name, "Request name")
        if not self.get_request(request_id):
            raise StorageError(f"Request {request_id} not found")
        try:
            self.db.execute(
                "UPDATE requests SET name = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (new_name, request_id),
            )
            logger.info("Renamed request %d to %r", request_id, new_name)
        except Exception as exc:
            raise StorageError(f"Failed to rename request: {exc}")

    def duplicate_request(self, request_id: int, new_name: str | None = None) -> int:
        """Duplicate a saved request within the same collection.

        Loads the original request via :meth:`get_request` and inserts it via
        :meth:`insert_request_row` so that **all** columns (including any added
        in future migrations) are automatically copied without needing to update
        this method.

        Args:
            request_id: Source request ID
            new_name: Name for the copy (defaults to "Copy of <original name>")

        Returns:
            New request ID

        Raises:
            StorageError: If source request not found
        """
        require_positive_int(request_id, "Request ID")
        source = self.get_request(request_id)
        if not source:
            raise StorageError(f"Request {request_id} not found")

        copy_name = new_name or f"Copy of {source.name}"
        if len(copy_name) > self.MAX_NAME_LENGTH:
            copy_name = copy_name[: self.MAX_NAME_LENGTH - 3] + "..."

        try:
            new_id = self.insert_request_row(
                source,
                source.collection_id,
                name_override=copy_name,
            )
            logger.info("Duplicated request %d → %d (%r)", request_id, new_id, copy_name)
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
        require_positive_int(collection_id, "Collection ID")

        collection = self.get_collection(collection_id)
        if not collection:
            raise StorageError(f"Collection with ID {collection_id} does not exist")

        try:
            count_row = self.db.fetchone(
                "SELECT COUNT(*) AS cnt FROM requests WHERE collection_id = ?",
                (collection_id,),
            )
            request_count = (count_row or {}).get("cnt", 0)
            self.db.execute("DELETE FROM collections WHERE id = ?", (collection_id,))
            logger.warning(
                f"Deleted collection '{collection['name']}' (ID: {collection_id}) "
                f"and {request_count} request(s)"
            )
        except Exception as exc:
            raise StorageError(f"Failed to delete collection: {exc}")

    def save_request(
        self, request: Request, collection_id: int | None = None, name: str | None = None
    ) -> int:
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

        effective_name = name or request.name or f"{request.method} {request.url}"

        try:
            req_id = self.insert_request_row(request, coll_id, name_override=effective_name)
            logger.info(
                "Saved request %r with ID %d to collection %s", effective_name, req_id, coll_id
            )
            return req_id
        except StorageError:
            raise
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
            name = name[: self.MAX_NAME_LENGTH - 3] + "..."
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
            logger.info("Updated auth on request %d", request_id)
        except Exception as exc:
            raise StorageError(f"Failed to update request auth: {exc}")

    def get_request(self, request_id: int) -> Request | None:
        """Get request by ID."""
        require_positive_int(request_id, "Request ID")
        row = self.db.fetchone("SELECT * FROM requests WHERE id = ?", (request_id,))
        if not row:
            return None
        return self._row_to_request(row)

    def list_requests(self, collection_id: int | None = None) -> list[dict[str, Any]]:
        """List requests, optionally filtered by collection."""
        if collection_id is not None:
            return self.db.fetchall(
                "SELECT * FROM requests WHERE collection_id = ? ORDER BY sort_order, name",
                (collection_id,),
            )
        return self.db.fetchall("SELECT * FROM requests ORDER BY collection_id, sort_order, name")

    def list_requests_in_collection(self, collection_id: int) -> list[dict[str, Any]]:
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
        require_positive_int(request_id, "Request ID")

        request = self.get_request(request_id)
        if not request:
            raise StorageError(f"Request with ID {request_id} does not exist")

        try:
            self.db.execute("DELETE FROM requests WHERE id = ?", (request_id,))
            logger.info("Deleted request %r (ID: %d)", request.name, request_id)
        except Exception as exc:
            raise StorageError(f"Failed to delete request: {exc}")

    def _row_to_request(self, row: dict[str, Any]) -> Request:
        """Convert a database row to a Request object."""
        headers = safe_json_loads(row.get("headers"), default={})
        params, params_list = _params_from_json(row.get("params", ""))
        auth = self._deserialize_auth(row.get("auth_type"), row.get("auth_data"))

        captures = safe_json_loads(row.get("captures"), default=[])
        if not isinstance(captures, list):
            captures = []

        assertions = safe_json_loads(row.get("assertions"), default=[])
        if not isinstance(assertions, list):
            assertions = []

        path_params = safe_json_loads(row.get("path_params"), default={})
        if not isinstance(path_params, dict):
            path_params = {}

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
            path_params=path_params,
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
        captures_json = self._serialize_list_field(request.captures, max_len=50_000)
        assertions_json = self._serialize_list_field(request.assertions, max_len=50_000)

        try:
            self.db.execute(
                """
                UPDATE requests SET
                    name=?, method=?, url=?, headers=?, params=?, body=?,
                    auth_type=?, auth_data=?,
                    timeout=?, verify_ssl=?, follow_redirects=?,
                    folder=?, description=?,
                    captures=?, assertions=?, pre_script=?, post_script=?,
                    cert_path=?, cert_key_path=?,
                    path_params=?,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (
                    request.name or "",
                    request.method,
                    request.url,
                    # See note above — preserve original-case header keys when
                    # serializing requests for storage.
                    self._serialize_json_field(
                        request.headers.as_canonical_dict(lowercase=False)
                        if hasattr(request.headers, "as_canonical_dict")
                        else (request.headers or {}),
                        max_len=100_000,
                        default="{}",
                    ),
                    _params_to_json(request),
                    request.body,
                    auth_type,
                    auth_data,
                    request.timeout,
                    int(request.verify_ssl),
                    int(request.follow_redirects),
                    request.folder or None,
                    request.description or "",
                    captures_json,
                    assertions_json,
                    request.pre_script or "",
                    request.post_script or "",
                    request.cert_path,
                    request.cert_key_path,
                    self._serialize_json_field(request.path_params, max_len=50_000, default="{}"),
                    request.id,
                ),
            )
            logger.info("Auto-saved request %d (%r)", request.id, request.name)
        except Exception as exc:
            raise StorageError(f"Failed to update request: {exc}") from exc
