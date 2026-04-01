"""Persistent cookie jar backed by SQLite.

Cookies are stored in the ``cookies`` table (created by migration v11).
``CookieJarManager`` exposes a simple CRUD interface plus helpers to
integrate with ``httpx`` and to absorb ``Set-Cookie`` response headers.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from equinox.core.exceptions import StorageError, ValidationError
from equinox.storage.utils import require_positive_int
from equinox.storage.database import Database

logger = logging.getLogger(__name__)


class CookieJarManager:
    """Manage a persistent cookie jar stored in SQLite.

    Args:
        db: Open :class:`~equinox.storage.database.Database` instance.
    """

    MAX_COOKIES = 500
    MAX_NAME_LEN = 256
    MAX_VALUE_LEN = 4096
    MAX_DOMAIN_LEN = 253
    MAX_PATH_LEN = 1024

    def __init__(self, db: Database) -> None:
        self.db = db

    # ── Validation helpers ────────────────────────────────────────────────────

    def _validate_name(self, name: str) -> None:
        if not isinstance(name, str) or not name.strip():
            raise ValidationError("Cookie name must be a non-empty string")
        if len(name) > self.MAX_NAME_LEN:
            raise ValidationError(f"Cookie name exceeds {self.MAX_NAME_LEN} characters")
        if "\r" in name or "\n" in name:
            raise ValidationError("Cookie name contains invalid characters")

    def _validate_value(self, value: str) -> None:
        if not isinstance(value, str):
            raise ValidationError("Cookie value must be a string")
        if len(value) > self.MAX_VALUE_LEN:
            raise ValidationError(f"Cookie value exceeds {self.MAX_VALUE_LEN} characters")
        if "\r" in value or "\n" in value:
            raise ValidationError("Cookie value contains invalid characters")

    def _validate_domain(self, domain: str) -> None:
        if not isinstance(domain, str):
            raise ValidationError("Cookie domain must be a string")
        if len(domain) > self.MAX_DOMAIN_LEN:
            raise ValidationError(f"Cookie domain exceeds {self.MAX_DOMAIN_LEN} characters")
        if "\r" in domain or "\n" in domain:
            raise ValidationError("Cookie domain contains invalid characters")

    def _validate_path(self, path: str) -> None:
        if not isinstance(path, str):
            raise ValidationError("Cookie path must be a string")
        if len(path) > self.MAX_PATH_LEN:
            raise ValidationError(f"Cookie path exceeds {self.MAX_PATH_LEN} characters")
        if "\r" in path or "\n" in path:
            raise ValidationError("Cookie path contains invalid characters")

    def _validate_expires(self, expires: Optional[str]) -> None:
        if expires is None:
            return
        if not isinstance(expires, str):
            raise ValidationError("Cookie expires must be a string or None")
        if len(expires) > 100:
            raise ValidationError("Cookie expires value too long")
        if "\r" in expires or "\n" in expires:
            raise ValidationError("Cookie expires contains invalid characters")

    # ── Public API ────────────────────────────────────────────────────────────

    def list_cookies(self) -> List[Dict[str, Any]]:
        """Return all stored cookies ordered by domain, name."""
        rows = self.db.fetchall(
            "SELECT id, name, value, domain, path, secure, http_only, expires, created_at "
            "FROM cookies ORDER BY domain, name"
        )
        return [self._row_to_dict(r) for r in rows]

    def get(self, cookie_id: int) -> Optional[Dict[str, Any]]:
        """Return a single cookie by id, or None if not found."""
        require_positive_int(cookie_id, "cookie_id")
        row = self.db.fetchone(
            "SELECT id, name, value, domain, path, secure, http_only, expires, created_at "
            "FROM cookies WHERE id = ?",
            (cookie_id,),
        )
        return self._row_to_dict(row) if row else None

    def add_cookie(
        self,
        name: str,
        value: str = "",
        domain: str = "",
        path: str = "/",
        secure: bool = False,
        http_only: bool = False,
        expires: Optional[str] = None,
    ) -> int:
        """Insert a cookie, replacing any existing entry with the same name+domain+path.

        Returns:
            The id of the inserted/replaced row.
        """
        self._validate_name(name)
        self._validate_value(value)
        self._validate_domain(domain or "")
        self._validate_path(path or "/")
        self._validate_expires(expires)

        row = self.db.fetchone("SELECT COUNT(*) AS cnt FROM cookies")
        count = row["cnt"] if row else 0
        if count >= self.MAX_COOKIES:
            raise StorageError(
                f"Cookie jar is full ({self.MAX_COOKIES} cookies). Delete some first."
            )

        try:
            row_id = self.db.insert(
                """
                INSERT INTO cookies (name, value, domain, path, secure, http_only, expires)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(name, domain, path) DO UPDATE SET
                    value     = excluded.value,
                    secure    = excluded.secure,
                    http_only = excluded.http_only,
                    expires   = excluded.expires
                """,
                (name, value, domain or "", path or "/", int(secure), int(http_only), expires),
            )
            # lastrowid is 0 on update (upsert) — look up the real id
            if not row_id:
                row_id = self._find_id(name, domain, path)
            return row_id
        except StorageError:
            raise
        except Exception as exc:
            raise StorageError(f"Failed to add cookie: {exc}") from exc

    def update_cookie(
        self,
        cookie_id: int,
        *,
        value: Optional[str] = None,
        secure: Optional[bool] = None,
        http_only: Optional[bool] = None,
        expires: Optional[str] = None,
    ) -> None:
        """Update mutable fields of an existing cookie."""
        require_positive_int(cookie_id, "cookie_id")
        if self.get(cookie_id) is None:
            raise StorageError(f"Cookie {cookie_id} not found")

        updates = []
        params: List[Any] = []
        if value is not None:
            self._validate_value(value)
            updates.append("value = ?")
            params.append(value)
        if secure is not None:
            updates.append("secure = ?")
            params.append(int(secure))
        if http_only is not None:
            updates.append("http_only = ?")
            params.append(int(http_only))
        if expires is not None:
            self._validate_expires(expires)
            updates.append("expires = ?")
            params.append(expires)

        if not updates:
            return
        params.append(cookie_id)
        self.db.execute(
            f"UPDATE cookies SET {', '.join(updates)} WHERE id = ?", tuple(params)
        )

    def delete_cookie(self, cookie_id: int) -> None:
        """Delete a cookie by id."""
        require_positive_int(cookie_id, "cookie_id")
        if self.get(cookie_id) is None:
            raise StorageError(f"Cookie {cookie_id} not found")
        self.db.execute("DELETE FROM cookies WHERE id = ?", (cookie_id,))

    def clear_cookies(self) -> None:
        """Delete all cookies."""
        self.db.execute("DELETE FROM cookies", ())

    def update_from_response(self, response_headers: Dict[str, str], url: str) -> None:
        """Parse ``Set-Cookie`` headers and upsert into the jar."""
        set_cookies: List[str] = []
        for k, v in response_headers.items():
            if k.lower() == "set-cookie":
                set_cookies.append(v)

        if not set_cookies:
            return

        try:
            parsed = urlparse(url)
            default_domain = parsed.netloc.split(":")[0]
        except Exception:
            default_domain = ""

        for raw in set_cookies:
            try:
                self._parse_and_upsert(raw, default_domain)
            except Exception as exc:
                logger.debug("Failed to parse Set-Cookie '%s': %s", raw[:80], exc)

    def to_httpx_cookies(self) -> Dict[str, str]:
        """Return a flat ``{name: value}`` dict for ``httpx.Client(cookies=...)``."""
        rows = self.db.fetchall("SELECT name, value FROM cookies")
        return {row["name"]: row["value"] for row in rows}

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _find_id(self, name: str, domain: str, path: str) -> int:
        row = self.db.fetchone(
            "SELECT id FROM cookies WHERE name = ? AND domain = ? AND path = ?",
            (name, domain or "", path or "/"),
        )
        return row["id"] if row else -1

    @staticmethod
    def _row_to_dict(row: Any) -> Dict[str, Any]:
        return {
            "id":         row["id"],
            "name":       row["name"],
            "value":      row["value"],
            "domain":     row["domain"],
            "path":       row["path"],
            "secure":     bool(row["secure"]),
            "http_only":  bool(row["http_only"]),
            "expires":    row["expires"],
            "created_at": row["created_at"],
        }

    def _parse_and_upsert(self, raw: str, default_domain: str) -> None:
        """Minimal Set-Cookie parser — handles the most common attributes."""
        parts = [p.strip() for p in raw.split(";")]
        if not parts:
            return

        first = parts[0]
        if "=" in first:
            name, _, value = first.partition("=")
            name = name.strip()
            value = value.strip()
        else:
            name = first.strip()
            value = ""

        if not name:
            return

        domain = default_domain
        path = "/"
        secure = False
        http_only = False
        expires: Optional[str] = None

        for attr in parts[1:]:
            lower = attr.lower()
            if lower.startswith("domain="):
                domain = attr[7:].strip().lstrip(".")
            elif lower.startswith("path="):
                path = attr[5:].strip() or "/"
            elif lower == "secure":
                secure = True
            elif lower == "httponly":
                http_only = True
            elif lower.startswith("expires="):
                expires = attr[8:].strip()

        try:
            self.add_cookie(
                name=name,
                value=value,
                domain=domain,
                path=path,
                secure=secure,
                http_only=http_only,
                expires=expires,
            )
        except Exception as exc:
            logger.debug("Failed to store cookie '%s': %s", name[:80], exc)
