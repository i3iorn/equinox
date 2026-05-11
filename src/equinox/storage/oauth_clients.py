"""OAuth2 client management — named, reusable OAuth2 credentials."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from equinox.core.auth_cipher import decrypt_auth_data, encrypt_auth_data
from equinox.core.exceptions import DuplicateError, StorageError, ValidationError
from equinox.storage.database import Database
from equinox.storage.utils import require_str as _require_str, safe_json_dumps, safe_json_loads

logger = logging.getLogger(__name__)

# Allowed grant types
GRANT_TYPES = ("client_credentials", "refresh_token", "password", "authorization_code")


class OAuthClientManager:
    """Manage named, reusable OAuth2 client credentials.

    Clients are stored independently of collections so the same credentials
    can be selected across any request without duplication.  One client can be
    marked as the *default*; the auth dialog pre-selects it automatically.

    Column layout (``oauth_clients`` table)
    ----------------------------------------
    id            – row PK
    name          – human label, unique
    token_url     – e.g. https://auth.example.com/oauth/token
    client_id     – OAuth2 client_id
    client_secret – OAuth2 client_secret (plain; protected by file-system permissions)
    scope         – space-separated scopes (optional)
    grant_type    – client_credentials | refresh_token | password | authorization_code
    extra_params  – JSON dict of additional token-endpoint params (optional)
    description   – free-text note
    is_default    – 0/1, at most one row is 1
    created_at / updated_at
    """

    MAX_NAME_LEN = 200
    MAX_URL_LEN = 2000
    MAX_ID_LEN = 500
    MAX_SECRET_LEN = 2000
    MAX_SCOPE_LEN = 1000
    MAX_DESC_LEN = 1000
    _ENC_PREFIX = "enc:"

    def __init__(self, db: Database) -> None:
        self.db = db

    # ── Create ────────────────────────────────────────────────────────

    def create_client(
        self,
        name: str,
        token_url: str,
        client_id: str,
        client_secret: str,
        scope: str = "",
        grant_type: str = "client_credentials",
        extra_params: Optional[Dict[str, str]] = None,
        description: str = "",
    ) -> int:
        """Create a new OAuth2 client.

        Returns the new row ID.

        Raises:
            ValidationError: bad input
            StorageError: duplicate name or DB error
        """
        name = _require_str(name, "name", self.MAX_NAME_LEN)
        token_url = _require_str(token_url, "token_url", self.MAX_URL_LEN, required=False)
        client_id = _require_str(client_id, "client_id", self.MAX_ID_LEN, required=False)
        client_secret = _require_str(client_secret, "client_secret", self.MAX_SECRET_LEN, required=False)
        encrypted_secret = self._encrypt_client_secret(client_secret)
        scope = _require_str(scope, "scope", self.MAX_SCOPE_LEN, required=False)
        description = _require_str(description, "description", self.MAX_DESC_LEN, required=False)
        self._validate_grant_type(grant_type)

        extra_json = safe_json_dumps(extra_params or {})

        try:
            row_id = self.db.insert(
                """
                INSERT INTO oauth_clients
                  (name, token_url, client_id, client_secret, scope,
                   grant_type, extra_params, description)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (name, token_url, client_id, encrypted_secret, scope,
                 grant_type, extra_json, description),
            )
            logger.info("Created OAuth2 client '%s' (id=%d)", name, row_id)
            return row_id
        except DuplicateError:
            raise DuplicateError(f"An OAuth2 client named '{name}' already exists")
        except StorageError:
            raise
        except Exception as exc:
            raise StorageError(f"Failed to create OAuth2 client: {exc}") from exc

    # ── Read ──────────────────────────────────────────────────────────

    def get_client(self, client_id: int) -> Optional[Dict[str, Any]]:
        """Return client row by DB id, or None."""
        row = self.db.fetchone(
            "SELECT * FROM oauth_clients WHERE id = ?", (client_id,)
        )
        return self._decode_and_maybe_migrate(row) if row else None

    def get_client_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Return client row by name, or None."""
        row = self.db.fetchone(
            "SELECT * FROM oauth_clients WHERE name = ?", (name,)
        )
        return self._decode_and_maybe_migrate(row) if row else None

    def get_default(self) -> Optional[Dict[str, Any]]:
        """Return the default client, or None."""
        row = self.db.fetchone(
            "SELECT * FROM oauth_clients WHERE is_default = 1 LIMIT 1"
        )
        return self._decode_and_maybe_migrate(row) if row else None

    def list_clients(self) -> List[Dict[str, Any]]:
        """Return all clients sorted by name."""
        rows = self.db.fetchall(
            "SELECT * FROM oauth_clients ORDER BY name"
        )
        return [self._decode_and_maybe_migrate(r) for r in rows]

    # ── Update ────────────────────────────────────────────────────────

    def update_client(
        self,
        client_id: int,
        name: Optional[str] = None,
        token_url: Optional[str] = None,
        client_id_val: Optional[str] = None,
        client_secret: Optional[str] = None,
        scope: Optional[str] = None,
        grant_type: Optional[str] = None,
        extra_params: Optional[Dict[str, str]] = None,
        description: Optional[str] = None,
    ) -> None:
        """Partially update a client.  Only supplied (non-None) fields are changed."""
        existing = self.get_client(client_id)
        if not existing:
            raise StorageError(f"OAuth2 client {client_id} not found")

        updates, params = [], []

        if name is not None:
            updates.append("name = ?")
            params.append(_require_str(name, "name", self.MAX_NAME_LEN))
        if token_url is not None:
            updates.append("token_url = ?")
            params.append(_require_str(token_url, "token_url", self.MAX_URL_LEN))
        if client_id_val is not None:
            updates.append("client_id = ?")
            params.append(_require_str(client_id_val, "client_id", self.MAX_ID_LEN))
        if client_secret is not None:
            updates.append("client_secret = ?")
            validated_secret = _require_str(client_secret, "client_secret", self.MAX_SECRET_LEN, required=False)
            params.append(self._encrypt_client_secret(validated_secret))
        if scope is not None:
            updates.append("scope = ?")
            params.append(_require_str(scope, "scope", self.MAX_SCOPE_LEN, required=False))
        if grant_type is not None:
            self._validate_grant_type(grant_type)
            updates.append("grant_type = ?")
            params.append(grant_type)
        if extra_params is not None:
            updates.append("extra_params = ?")
            params.append(safe_json_dumps(extra_params))
        if description is not None:
            updates.append("description = ?")
            params.append(_require_str(description, "description", self.MAX_DESC_LEN, required=False))

        if not updates:
            return

        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.append(client_id)
        try:
            self.db.execute(
                f"UPDATE oauth_clients SET {', '.join(updates)} WHERE id = ?",
                tuple(params),
            )
            logger.info("Updated OAuth2 client id=%d", client_id)
        except DuplicateError:
            display_name = name if name is not None else existing["name"]
            raise DuplicateError(f"An OAuth2 client named '{display_name}' already exists")
        except StorageError:
            raise
        except Exception as exc:
            raise StorageError(f"Failed to update OAuth2 client: {exc}") from exc

    def set_default(self, client_id: int) -> None:
        """Mark one client as the default; clears any previous default.

        Uses a single atomic UPDATE to avoid a window where no client
        is marked as default.
        """
        if not self.get_client(client_id):
            raise StorageError(f"OAuth2 client {client_id} not found")
        self.db.execute(
            "UPDATE oauth_clients SET is_default = CASE WHEN id = ? THEN 1 ELSE 0 END",
            (client_id,),
        )
        logger.info("Set OAuth2 client id=%d as default", client_id)

    def clear_default(self) -> None:
        """Remove the default flag from all clients."""
        self.db.execute("UPDATE oauth_clients SET is_default = 0", ())

    # ── Delete ────────────────────────────────────────────────────────

    def delete_client(self, client_id: int) -> None:
        """Delete a client by DB id."""
        existing = self.get_client(client_id)
        if not existing:
            raise StorageError(f"OAuth2 client {client_id} not found")
        self.db.execute("DELETE FROM oauth_clients WHERE id = ?", (client_id,))
        logger.info("Deleted OAuth2 client '%s' (id=%d)", existing["name"], client_id)

    # ── Helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _validate_grant_type(grant_type: str) -> None:
        """Raise ``ValidationError`` if *grant_type* is not in ``GRANT_TYPES``."""
        if grant_type not in GRANT_TYPES:
            raise ValidationError(f"grant_type must be one of: {', '.join(GRANT_TYPES)}")

    def to_oauth2_auth(self, client: Dict[str, Any]) -> "OAuth2Auth":
        """Build a live :class:`~equinox.auth.oauth2.OAuth2Auth` from a client row."""
        from equinox.auth._oauth2 import OAuth2Auth
        return OAuth2Auth(
            token_url=client["token_url"],
            client_id=client["client_id"],
            client_secret=client["client_secret"],
            scope=client.get("scope") or None,
            extra_params=client.get("extra_params"),
        )

    def _decode_and_maybe_migrate(self, row) -> Dict[str, Any]:
        d = dict(row)
        raw_secret = d.get("client_secret")
        d["client_secret"] = self._decrypt_client_secret(raw_secret)
        d["extra_params"] = safe_json_loads(d.get("extra_params") or "{}", row_id=d.get("id"))
        d["is_default"] = bool(d.get("is_default", 0))
        if self._is_legacy_plaintext_secret(raw_secret):
            self._migrate_legacy_client_secret(d["id"], d["client_secret"])
        return d

    @classmethod
    def _encrypt_client_secret(cls, secret: str) -> str:
        if not secret:
            return secret
        if secret.startswith(cls._ENC_PREFIX):
            return secret
        return encrypt_auth_data(secret)

    @classmethod
    def _decrypt_client_secret(cls, stored: Optional[str]) -> str:
        if not stored:
            return ""
        return decrypt_auth_data(stored)

    @classmethod
    def _is_legacy_plaintext_secret(cls, stored: Optional[str]) -> bool:
        return bool(stored and isinstance(stored, str) and not stored.startswith(cls._ENC_PREFIX))

    def _migrate_legacy_client_secret(self, client_id: int, plaintext_secret: str) -> None:
        encrypted = self._encrypt_client_secret(plaintext_secret)
        if encrypted == plaintext_secret:
            return
        try:
            self.db.execute(
                "UPDATE oauth_clients SET client_secret = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (encrypted, client_id),
            )
            logger.info("Migrated legacy OAuth2 client secret at read-time (id=%d)", client_id)
        except Exception as exc:
            logger.warning(
                "Failed to migrate legacy OAuth2 client secret at read-time (id=%d): %s",
                client_id,
                exc,
            )


