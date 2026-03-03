"""Auth serialization and resolution methods for CollectionManager."""

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class CollectionAuthMixin:
    """Mixin providing auth configuration for CollectionManager."""

    # ── Serialization helpers ──────────────────────────────────────────

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
