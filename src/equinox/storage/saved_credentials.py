"""Saved credential management — store and reuse named auth configurations.

Supports any auth type: OAuth2, API Key, Basic Auth, Bearer Token.
Each credential is stored as a name + auth_type discriminator + config JSON blob.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from equinox.storage.database import Database
from equinox.core.exceptions import StorageError, ValidationError
from equinox.core.auth_cipher import encrypt_auth_data, decrypt_auth_data
from equinox.storage.utils import require_str as _require_str

logger = logging.getLogger(__name__)

AUTH_TYPES = ("oauth2", "api_key", "basic", "bearer", "aws_sigv4")

AUTH_TYPE_LABELS: Dict[str, str] = {
    "oauth2":    "OAuth 2.0",
    "api_key":   "API Key",
    "basic":     "Basic Auth",
    "bearer":    "Bearer Token",
    "aws_sigv4": "AWS SigV4",
}


class SavedCredentialsManager:
    """Manage named, reusable auth credentials of any supported type.

    Each row in ``saved_credentials`` holds:
    - ``name``      – unique human label
    - ``auth_type`` – one of :data:`AUTH_TYPES`
    - ``config``    – JSON dict whose keys depend on auth_type
    - ``description``, ``is_default``, timestamps

    Config shapes per type
    ----------------------
    oauth2:  token_url, client_id, client_secret, scope, grant_type, extra_params
    api_key: key, value, location  (location = "header" | "query")
    basic:   username, password
    bearer:  token
    """

    MAX_NAME_LEN = 200
    MAX_DESC_LEN = 1000

    def __init__(self, db: Database) -> None:
        self.db = db

    # ── Create ────────────────────────────────────────────────────────

    def create(
        self,
        name: str,
        auth_type: str,
        config: Optional[Dict[str, Any]] = None,
        description: str = "",
        is_default: bool = False,
    ) -> int:
        """Create a new saved credential.  Returns the new row ID.

        Raises:
            ValidationError: bad input
            StorageError: duplicate name or DB error
        """
        name        = self._req_str(name, "name", self.MAX_NAME_LEN)
        description = self._opt_str(description, "description", self.MAX_DESC_LEN)
        if auth_type not in AUTH_TYPES:
            raise ValidationError(
                f"auth_type must be one of: {', '.join(AUTH_TYPES)}"
            )
        config_json = encrypt_auth_data(json.dumps(config or {}))
        try:
            row_id = self.db.insert(
                """
                INSERT INTO saved_credentials
                  (name, auth_type, config, description, is_default)
                VALUES (?, ?, ?, ?, ?)
                """,
                (name, auth_type, config_json, description, 1 if is_default else 0),
            )
            logger.info(
                "Created saved credential '%s' (type=%s, id=%d)", name, auth_type, row_id
            )
            return row_id
        except Exception as exc:
            if "UNIQUE constraint" in str(exc):
                raise StorageError(
                    f"A saved credential named '{name}' already exists"
                )
            raise StorageError(f"Failed to create saved credential: {exc}") from exc

    # ── Read ──────────────────────────────────────────────────────────

    def get(self, cred_id: int) -> Optional[Dict[str, Any]]:
        """Return credential by DB id, or None."""
        row = self.db.fetchone(
            "SELECT * FROM saved_credentials WHERE id = ?", (cred_id,)
        )
        return self._decode(row) if row else None

    def get_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Return credential by name, or None."""
        row = self.db.fetchone(
            "SELECT * FROM saved_credentials WHERE name = ?", (name,)
        )
        if row:
            logger.debug("Found saved credential by name: %s (id=%d)", name, row["id"])
        return self._decode(row) if row else None

    def get_default(self, auth_type: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Return the default credential (optionally filtered by auth_type)."""
        if auth_type:
            row = self.db.fetchone(
                "SELECT * FROM saved_credentials"
                " WHERE is_default = 1 AND auth_type = ? LIMIT 1",
                (auth_type,),
            )
        else:
            row = self.db.fetchone(
                "SELECT * FROM saved_credentials WHERE is_default = 1 LIMIT 1"
            )
        return self._decode(row) if row else None

    def list(self, auth_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return all credentials sorted by auth_type then name."""
        if auth_type:
            rows = self.db.fetchall(
                "SELECT * FROM saved_credentials WHERE auth_type = ? ORDER BY name",
                (auth_type,),
            )
        else:
            rows = self.db.fetchall(
                "SELECT * FROM saved_credentials ORDER BY auth_type, name"
            )
        return [self._decode(r) for r in rows]

    # ── Update ────────────────────────────────────────────────────────

    def update(
        self,
        cred_id: int,
        name: Optional[str] = None,
        auth_type: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
        description: Optional[str] = None,
    ) -> None:
        """Partially update a credential.  Only non-None fields are changed."""
        if not self.get(cred_id):
            raise StorageError(f"Saved credential {cred_id} not found")

        updates: List[str] = []
        params: List[Any] = []

        if name is not None:
            updates.append("name = ?")
            params.append(self._req_str(name, "name", self.MAX_NAME_LEN))
        if auth_type is not None:
            if auth_type not in AUTH_TYPES:
                raise ValidationError(
                    f"auth_type must be one of: {', '.join(AUTH_TYPES)}"
                )
            updates.append("auth_type = ?")
            params.append(auth_type)
        if config is not None:
            updates.append("config = ?")
            params.append(encrypt_auth_data(json.dumps(config)))
        if description is not None:
            updates.append("description = ?")
            params.append(self._opt_str(description, "description", self.MAX_DESC_LEN))

        if not updates:
            return

        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.append(cred_id)
        try:
            self.db.execute(
                f"UPDATE saved_credentials SET {', '.join(updates)} WHERE id = ?",
                tuple(params),
            )
            logger.info("Updated saved credential id=%d", cred_id)
        except Exception as exc:
            if "UNIQUE constraint" in str(exc):
                raise StorageError(
                    f"A saved credential named '{name}' already exists"
                )
            raise StorageError(f"Failed to update saved credential: {exc}") from exc

    def set_default(self, cred_id: int) -> None:
        """Mark one credential as the default; clears any previous default.

        Uses a single atomic UPDATE to avoid a window where no credential
        is marked as default (which would happen if the app crashed between
        two separate UPDATE statements).
        """
        if not self.get(cred_id):
            raise StorageError(f"Saved credential {cred_id} not found")
        self.db.execute(
            "UPDATE saved_credentials SET is_default = CASE WHEN id = ? THEN 1 ELSE 0 END",
            (cred_id,),
        )
        logger.info("Set saved credential id=%d as default", cred_id)

    def clear_default(self) -> None:
        """Remove the default flag from all credentials."""
        self.db.execute("UPDATE saved_credentials SET is_default = 0", ())

    # ── Duplicate ─────────────────────────────────────────────────────

    def duplicate(self, cred_id: int, new_name: Optional[str] = None) -> int:
        """Create an exact copy of a credential.  Returns the new row ID.

        If *new_name* is not supplied, a unique "(Copy)" / "(Copy 2)" suffix
        is appended to the source name automatically.
        """
        source = self.get(cred_id)
        if not source:
            logger.warning("Attempted to duplicate non-existent credential id=%d", cred_id)
            raise StorageError(f"Saved credential {cred_id} not found")

        if new_name is None:
            new_name = self._unique_copy_name(source["name"])

        new_id = self.create(
            name=new_name,
            auth_type=source["auth_type"],
            config=source["config"],
            description=source.get("description", ""),
        )
        logger.info(
            "Duplicated saved credential id=%d to new_id=%d (new_name=%s)",
            cred_id, new_id, new_name,
        )
        return new_id

    _MAX_COPY_ATTEMPTS = 100

    def _unique_copy_name(self, base_name: str) -> str:
        """Return a unique 'base_name (Copy)' / 'base_name (Copy 2)' label."""
        candidate = f"{base_name} (Copy)"
        if not self.get_by_name(candidate):
            return candidate
        for n in range(2, self._MAX_COPY_ATTEMPTS + 2):
            candidate = f"{base_name} (Copy {n})"
            if not self.get_by_name(candidate):
                return candidate
        raise StorageError(
            f"Could not generate unique name after {self._MAX_COPY_ATTEMPTS} attempts"
        )

    # ── Delete ────────────────────────────────────────────────────────

    def delete(self, cred_id: int) -> None:
        """Delete a credential by DB id."""
        existing = self.get(cred_id)
        if not existing:
            logger.warning("Attempted to delete non-existent credential id=%d", cred_id)
            raise StorageError(f"Saved credential {cred_id} not found")
        
        self.db.execute("DELETE FROM saved_credentials WHERE id = ?", (cred_id,))
        logger.info("Deleted saved credential id=%d (name=%s)", cred_id, existing.get("name"))
        if not existing:
            raise StorageError(f"Saved credential {cred_id} not found")
        self.db.execute("DELETE FROM saved_credentials WHERE id = ?", (cred_id,))
        logger.info(
            "Deleted saved credential '%s' (id=%d)", existing["name"], cred_id
        )

    # ── Auth strategy factory ─────────────────────────────────────────

    def to_auth_strategy(self, row: Dict[str, Any]):
        """Build a live auth-strategy object from a saved credential row.

        Imports are lazy to avoid circular dependencies.

        Raises:
            StorageError: If the credential config is invalid or incomplete
                (e.g. empty token/password from a legacy row).
        """
        from equinox.auth.oauth2 import OAuth2Auth
        from equinox.auth.api_key import APIKeyAuth
        from equinox.auth.basic import BasicAuth
        from equinox.auth.bearer import BearerAuth

        auth_type = row["auth_type"]
        cfg = row["config"]

        try:
            if auth_type == "oauth2":
                return OAuth2Auth(
                    token_url=cfg.get("token_url") or None,
                    client_id=cfg.get("client_id") or None,
                    client_secret=cfg.get("client_secret") or None,
                    scope=cfg.get("scope") or None,
                )
            if auth_type == "api_key":
                return APIKeyAuth(
                    key=cfg.get("key", "X-API-Key"),
                    value=cfg.get("value", ""),
                    location=cfg.get("location", "header"),
                )
            if auth_type == "basic":
                return BasicAuth(
                    username=cfg.get("username", ""),
                    password=cfg.get("password", ""),
                )
            if auth_type == "bearer":
                return BearerAuth(token=cfg.get("token", ""))
            if auth_type == "aws_sigv4":
                from equinox.auth.aws_sigv4 import AWSSigV4Auth
                return AWSSigV4Auth(
                    access_key=cfg.get("access_key", ""),
                    secret_key=cfg.get("secret_key", ""),
                    region=cfg.get("region", "us-east-1"),
                    service=cfg.get("service", "execute-api"),
                    session_token=cfg.get("session_token") or None,
                )
        except Exception as exc:
            raise StorageError(
                f"Saved credential '{row.get('name', '?')}' has invalid config: {exc}"
            ) from exc

        raise ValidationError(f"Unknown auth_type: {auth_type!r}")

    # ── Aliases (for backward-compat with test expectations) ──────────

    create_credential  = create
    get_credential     = get
    list_credentials   = list
    delete_credential  = delete
    update_credential  = update

    # ── Helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _decode(row) -> Dict[str, Any]:
        d = dict(row)
        try:
            raw_config = d.get("config") or "{}"
            d["config"] = json.loads(decrypt_auth_data(raw_config))
        except (json.JSONDecodeError, TypeError):
            d["config"] = {}
        d["is_default"] = bool(d.get("is_default", 0))
        return d

    @staticmethod
    def _req_str(value: Any, field: str, max_len: int) -> str:
        return _require_str(value, field, max_len, required=True)

    @staticmethod
    def _opt_str(value: Any, field: str, max_len: int) -> str:
        return _require_str(value, field, max_len, required=False)
