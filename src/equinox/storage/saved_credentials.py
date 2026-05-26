"""Saved credential management — store and reuse named auth configurations.

Supports any auth type: OAuth2, API Key, Basic Auth, Bearer Token, AWS SigV4.
Each credential is stored as a name + auth_type discriminator + config JSON blob.

Type lists and display labels are derived from the auth factory registry
(:mod:`equinox.auth`) — no parallel constants to maintain.
"""

import logging
from collections.abc import Mapping
from typing import Any

from equinox.auth import AUTH_TYPES
from equinox.core.exceptions import DuplicateError, SecurityError, StorageError, ValidationError
from equinox.storage.auth_cipher_storage import (
    decrypt_auth_storage_value,
    encrypt_auth_storage_value,
)
from equinox.storage.database import Database
from equinox.storage.utils import (
    MAX_DESCRIPTION_LENGTH,
    MAX_NAME_LENGTH,
    require_str,
    safe_json_dumps,
    safe_json_loads,
)

logger = logging.getLogger(__name__)


def _get_auth_types() -> tuple[str, ...]:
    """Lazy accessor for canonical auth types (avoids circular import)."""
    from equinox.auth import get_auth_types

    return get_auth_types()


def _get_auth_type_labels() -> dict[str, str]:
    """Lazy accessor for auth type display labels (avoids circular import)."""
    from equinox.auth import get_auth_type_labels

    return get_auth_type_labels()


# Maximum serialized JSON length for a stored config blob.
_MAX_CONFIG_JSON_LEN: int = 50_000


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

    _MAX_COPY_ATTEMPTS = 100

    def __init__(self, db: Database) -> None:
        self.db = db

    # ── Create ────────────────────────────────────────────────────────

    def create(
        self,
        name: str,
        auth_type: str,
        config: dict[str, Any] | None = None,
        description: str = "",
        is_default: bool = False,
    ) -> int:
        """Create a new saved credential.  Returns the new row ID.

        Raises:
            ValidationError: bad input
            StorageError: duplicate name or DB error
        """
        name = require_str(name, "name", MAX_NAME_LENGTH)
        description = require_str(
            description, "description", MAX_DESCRIPTION_LENGTH, required=False
        )
        self._validate_auth_type(auth_type)
        config_json = self._serialize_config(config or {})
        try:
            row_id = self.db.insert(
                """
                INSERT INTO saved_credentials
                  (name, auth_type, config, description, is_default)
                VALUES (?, ?, ?, ?, ?)
                """,
                (name, auth_type, config_json, description, 1 if is_default else 0),
            )
            logger.info("Created saved credential '%s' (type=%s, id=%d)", name, auth_type, row_id)
            return row_id
        except DuplicateError:
            raise DuplicateError(f"A saved credential named '{name}' already exists")
        except Exception as exc:
            raise StorageError(f"Failed to create saved credential: {exc}") from exc

    # ── Read ──────────────────────────────────────────────────────────

    def get(self, cred_id: int) -> dict[str, Any] | None:
        """Return credential by DB id, or None."""
        row = self.db.fetchone("SELECT * FROM saved_credentials WHERE id = ?", (cred_id,))
        return self._decode(row) if row else None

    def get_by_name(self, name: str) -> dict[str, Any] | None:
        """Return credential by name, or None."""
        row = self.db.fetchone("SELECT * FROM saved_credentials WHERE name = ?", (name,))
        if row:
            logger.debug("Found saved credential by name: %s (id=%d)", name, row["id"])
        return self._decode(row) if row else None

    def get_default(self, auth_type: str | None = None) -> dict[str, Any] | None:
        """Return the default credential (optionally filtered by auth_type)."""
        if auth_type:
            row = self.db.fetchone(
                "SELECT * FROM saved_credentials" " WHERE is_default = 1 AND auth_type = ? LIMIT 1",
                (auth_type,),
            )
        else:
            row = self.db.fetchone("SELECT * FROM saved_credentials WHERE is_default = 1 LIMIT 1")
        return self._decode(row) if row else None

    def list(self, auth_type: str | None = None) -> list[dict[str, Any]]:
        """Return all credentials sorted by auth_type then name."""
        if auth_type:
            rows = self.db.fetchall(
                "SELECT * FROM saved_credentials WHERE auth_type = ? ORDER BY name",
                (auth_type,),
            )
        else:
            rows = self.db.fetchall("SELECT * FROM saved_credentials ORDER BY auth_type, name")
        return [self._decode(r) for r in rows]

    # ── Update ────────────────────────────────────────────────────────

    def update(
        self,
        cred_id: int,
        name: str | None = None,
        auth_type: str | None = None,
        config: dict[str, Any] | None = None,
        description: str | None = None,
    ) -> None:
        """Partially update a credential.  Only non-None fields are changed."""
        self._require_existing(cred_id)

        updates: list[str] = []
        params: list[Any] = []
        validated_name: str | None = None

        if name is not None:
            validated_name = require_str(name, "name", MAX_NAME_LENGTH)
            updates.append("name = ?")
            params.append(validated_name)
        if auth_type is not None:
            self._validate_auth_type(auth_type)
            updates.append("auth_type = ?")
            params.append(auth_type)
        if config is not None:
            updates.append("config = ?")
            params.append(self._serialize_config(config))
        if description is not None:
            updates.append("description = ?")
            params.append(
                require_str(description, "description", MAX_DESCRIPTION_LENGTH, required=False)
            )

        if not updates:
            return

        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.append(cred_id)
        try:
            # updates contains only hardcoded "col = ?" literals — no user data in the SQL string.
            self.db.execute(
                f"UPDATE saved_credentials SET {', '.join(updates)} WHERE id = ?",  # nosec B608
                tuple(params),
            )
            logger.info("Updated saved credential id=%d", cred_id)
        except DuplicateError:
            raise DuplicateError(f"A saved credential named '{validated_name}' already exists")
        except Exception as exc:
            raise StorageError(f"Failed to update saved credential: {exc}") from exc

    def set_default(self, cred_id: int) -> None:
        """Mark one credential as the default; clears any previous default.

        Uses a single atomic UPDATE to avoid a window where no credential
        is marked as default (which would happen if the app crashed between
        two separate UPDATE statements).
        """
        self._require_existing(cred_id)
        self.db.execute(
            "UPDATE saved_credentials SET is_default = CASE WHEN id = ? THEN 1 ELSE 0 END",
            (cred_id,),
        )
        logger.info("Set saved credential id=%d as default", cred_id)

    def clear_default(self) -> None:
        """Remove the default flag from all credentials."""
        self.db.execute("UPDATE saved_credentials SET is_default = 0", ())

    # ── Duplicate ─────────────────────────────────────────────────────

    def duplicate(self, cred_id: int, new_name: str | None = None) -> int:
        """Create an exact copy of a credential.  Returns the new row ID.

        If *new_name* is not supplied, a unique "(Copy)" / "(Copy 2)" suffix
        is appended to the source name automatically.
        """
        source = self._require_existing(cred_id)

        resolved_name: str = (
            new_name if new_name is not None else self._unique_copy_name(source["name"])
        )

        new_id = self.create(
            name=resolved_name,
            auth_type=source["auth_type"],
            config=source["config"],
            description=source.get("description", ""),
        )
        logger.info(
            "Duplicated saved credential id=%d to new_id=%d (new_name=%s)",
            cred_id,
            new_id,
            resolved_name,
        )
        return new_id

    def suggest_copy_name(self, base_name: str) -> str:
        """Return the next available copy-style name for *base_name*."""
        validated_name = require_str(base_name, "base_name", MAX_NAME_LENGTH)
        return self._unique_copy_name(validated_name)

    def _unique_copy_name(self, base_name: str) -> str:
        """Return a unique 'base_name (Copy)' / 'base_name (Copy 2)' label.

        Uses a single SQL query to fetch all matching names, then picks the
        first unused suffix in Python — avoiding up to 101 round-trips.
        """
        # Escape LIKE metacharacters so special characters (%, _, \) in
        # credential names are treated as literals.
        escaped = base_name.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        rows = self.db.fetchall(
            "SELECT name FROM saved_credentials WHERE name LIKE ? ESCAPE '\\'",
            (f"{escaped} (Copy%",),
        )
        existing = {r["name"] for r in rows}

        candidate = f"{base_name} (Copy)"
        if candidate not in existing:
            return candidate
        for n in range(2, self._MAX_COPY_ATTEMPTS + 2):
            candidate = f"{base_name} (Copy {n})"
            if candidate not in existing:
                return candidate
        raise StorageError(
            f"Could not generate unique name after {self._MAX_COPY_ATTEMPTS} attempts"
        )

    # ── Delete ────────────────────────────────────────────────────────

    def delete(self, cred_id: int) -> None:
        """Delete a credential by DB id."""
        existing = self._require_existing(cred_id)
        self.db.execute("DELETE FROM saved_credentials WHERE id = ?", (cred_id,))
        logger.info("Deleted saved credential '%s' (id=%d)", existing.get("name"), cred_id)

    # ── Auth strategy factory ─────────────────────────────────────────

    def to_auth_strategy(self, row: dict[str, Any]) -> Any:
        """Build a live auth-strategy object from a saved credential row.

        Delegates to the unified :func:`~equinox.auth.auth_from_dict`
        registry so new auth types only need to be registered in one place.

        Raises:
            ValidationError: If *auth_type* is not recognized.
            StorageError: If the credential config is invalid or incomplete.
        """
        from equinox.auth import auth_from_dict

        auth_type = row["auth_type"]
        cfg = row["config"]

        # Normalise config keys to match from_dict() expectations.
        # Saved-credential config uses the same keys as to_dict() for most types.
        # For OAuth2, saved configs omit token state (access_token, refresh_token)
        # which is correct — the strategy will fetch fresh tokens on demand.
        try:
            result = auth_from_dict(auth_type, cfg)
            if result is None:
                raise StorageError(f"Saved credential '{row.get('name', '?')}' has invalid config")
            return result
        except ValueError:
            raise ValidationError(f"Unknown auth_type: {auth_type!r}")
        except Exception as exc:
            raise StorageError(
                f"Saved credential '{row.get('name', '?')}' has invalid config: {exc}"
            ) from exc

    # ── Aliases (for backward-compat with test expectations) ──────────

    create_credential = create
    get_credential = get
    list_credentials = list
    delete_credential = delete
    update_credential = update

    # ── Private helpers ───────────────────────────────────────────────

    def _require_existing(self, cred_id: int) -> dict[str, Any]:
        """Return the decoded row for *cred_id*, or raise :class:`StorageError`."""
        row = self.get(cred_id)
        if not row:
            raise StorageError(f"Saved credential {cred_id} not found")
        return row

    @staticmethod
    def _validate_auth_type(auth_type: str) -> None:
        """Raise :class:`ValidationError` if *auth_type* is not in :data:`AUTH_TYPES`."""
        if auth_type not in AUTH_TYPES:
            raise ValidationError(f"auth_type must be one of: {', '.join(AUTH_TYPES)}")

    @staticmethod
    def _serialize_config(config: dict[str, Any]) -> str:
        """Serialize and encrypt a config dict for storage.

        Raises:
            ValidationError: If the serialized JSON exceeds :data:`_MAX_CONFIG_JSON_LEN`.
        """
        try:
            json_str = safe_json_dumps(config, max_len=_MAX_CONFIG_JSON_LEN)
        except SecurityError as exc:
            raise ValidationError(f"Credential config too large: {exc}") from exc
        return encrypt_auth_storage_value(json_str)

    @staticmethod
    def _decode(row: Any) -> dict[str, Any]:
        if not isinstance(row, Mapping):
            raise StorageError("Failed to decode stored credential row: expected mapping")
        d: dict[str, Any] = dict(row)
        raw_config = d.get("config") or "{}"
        try:
            d["config"] = safe_json_loads(decrypt_auth_storage_value(raw_config))
        except SecurityError:
            # Decryption failures indicate key mismatch or tampering — propagate
            # rather than silently returning an empty config, which would mask a
            # security-relevant event (consistent with CollectionAuthMixin).
            raise
        except Exception as exc:
            # Any other failure (e.g. key-file I/O error, encoding error) is
            # equally security-relevant — wrap and propagate instead of hiding it.
            raise StorageError(f"Failed to decode stored credential config: {exc}") from exc
        d["is_default"] = bool(d.get("is_default", 0))
        return d
