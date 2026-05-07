"""Auth serialization and resolution methods for CollectionManager."""

import logging
from typing import Optional

from equinox.core.auth_cipher import encrypt_auth_data, decrypt_auth_data
from equinox.core.exceptions import StorageError, SecurityError
from equinox.storage.utils import safe_json_dumps, safe_json_loads

logger = logging.getLogger(__name__)


class CollectionAuthMixin:
    """Mixin providing auth configuration for CollectionManager."""

    # ── Serialization helpers ──────────────────────────────────────────

    @staticmethod
    def _serialize_auth(auth) -> "tuple[Optional[str], Optional[str]]":
        """Return (auth_type, encrypted_auth_data) from an auth strategy object or None.

        The JSON blob is encrypted at rest via :func:`encrypt_auth_data`.

        Raises:
            StorageError: If the auth object cannot be serialized.
        """
        if auth is None:
            return None, None
        try:
            d = auth.to_dict()
            blob = safe_json_dumps(d, max_len=50_000)
            return d.get("type"), encrypt_auth_data(blob)
        except Exception as exc:
            raise StorageError(
                f"Failed to serialize auth ({type(auth).__name__}): {exc}"
            ) from exc

    @staticmethod
    def _deserialize_auth(auth_type: "Optional[str]", auth_data: "Optional[str]"):
        """Return an auth strategy object from DB columns, or None.

        Handles both encrypted (``enc:…``) and legacy plaintext JSON
        transparently via :func:`decrypt_auth_data`.

        Delegates the type→class dispatch to the unified
        :func:`~equinox.auth.auth_from_dict` registry so new auth
        types only need to be registered in one place.
        """
        if not auth_type or not auth_data:
            return None
        try:
            # Decryption failures are considered security-sensitive and should
            # be surfaced to callers so they can prompt for recovery / key
            # rotation.  JSON errors (malformed blobs) are still treated as
            # 'no auth' to avoid breaking UI flows.
            from equinox.core.exceptions import SecurityError as _SEC

            try:
                raw = decrypt_auth_data(auth_data)
            except _SEC:
                logger.exception("Auth decryption failed for auth_data column")
                # Propagate so higher-level code (CLI/GUI) can surface a clear error
                raise

            d = safe_json_loads(raw)
            if not d:
                return None
        except SecurityError:
            # Let SecurityError bubble up to callers — do not swallow.
            raise
        except Exception:
            # Malformed JSON or other non-security errors degrade to no-auth
            logger.debug("Malformed auth JSON in DB row — treating as no auth", exc_info=True)
            return None

        # Use the embedded "type" key when available; fall back to the
        # auth_type column stored alongside the blob.
        from equinox.auth import auth_from_dict
        return auth_from_dict(auth_type, d)

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

        Uses a single UNION query to fetch all candidate rows in one round-trip
        instead of issuing one query per folder-path segment.

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

        # Build ancestor list deepest-first (e.g. "A/B/C" → ["A/B/C", "A/B", "A"])
        ancestors: list = []
        folder = request.folder
        if folder:
            parts = folder.split("/")
            for depth in range(len(parts), 0, -1):
                ancestors.append("/".join(parts[:depth]))

        # Single UNION query: fetch all matching folder rows + the collection row.
        if ancestors:
            placeholders = ",".join("?" * len(ancestors))
            query = (
                f"SELECT 'folder' AS source, path, auth_type, auth_data "
                f"FROM collection_folders "
                f"WHERE collection_id=? AND path IN ({placeholders}) "
                f"AND auth_type IS NOT NULL "
                f"UNION ALL "
                f"SELECT 'collection' AS source, NULL AS path, auth_type, auth_data "
                f"FROM collections "
                f"WHERE id=? AND auth_type IS NOT NULL"
            )
            rows = self.db.fetchall(query, (collection_id, *ancestors, collection_id))
        else:
            rows = self.db.fetchall(
                "SELECT 'collection' AS source, NULL AS path, auth_type, auth_data "
                "FROM collections WHERE id=? AND auth_type IS NOT NULL",
                (collection_id,),
            )

        if not rows:
            return None, None

        # Index folder rows by path for O(1) lookup
        folder_rows = {r["path"]: r for r in rows if r["source"] == "folder"}
        collection_row = next((r for r in rows if r["source"] == "collection"), None)

        # Return deepest matching folder auth first
        for ancestor in ancestors:
            row = folder_rows.get(ancestor)
            if row:
                auth = self._deserialize_auth(row["auth_type"], row["auth_data"])
                if auth is not None:
                    return auth, f"folder:{ancestor}"

        # Fall back to collection auth
        if collection_row:
            auth = self._deserialize_auth(
                collection_row["auth_type"], collection_row["auth_data"]
            )
            if auth is not None:
                return auth, "collection"

        return None, None
