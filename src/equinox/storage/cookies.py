"""Persistent cookie jar backed by SQLite.

Cookies are stored in the ``cookies`` table (created by migration v11).
``CookieJarManager`` exposes a simple CRUD interface plus helpers to
integrate with ``httpx`` and to absorb ``Set-Cookie`` response headers.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from equinox.core.exceptions import StorageError, ValidationError
from equinox.core import urls
from equinox.storage.utils import require_positive_int
from equinox.storage.database import Database

logger = logging.getLogger(__name__)


# ── Module-level validation helper ───────────────────────────────────────────

def _validate_str_field(
    field_name: str,
    value: object,
    max_len: int,
    *,
    required: bool = False,
) -> None:
    """Validate a cookie string field: type, optional non-empty, length, CRLF."""
    if not isinstance(value, str):
        raise ValidationError(f"Cookie {field_name} must be a string")
    if required and not value.strip():
        raise ValidationError(f"Cookie {field_name} must be a non-empty string")
    if len(value) > max_len:
        raise ValidationError(f"Cookie {field_name} exceeds {max_len} characters")
    if "\r" in value or "\n" in value:
        raise ValidationError(f"Cookie {field_name} contains invalid characters")


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
        _validate_str_field("name", name, self.MAX_NAME_LEN, required=True)

    def _validate_value(self, value: str) -> None:
        _validate_str_field("value", value, self.MAX_VALUE_LEN)

    def _validate_domain(self, domain: str) -> None:
        _validate_str_field("domain", domain, self.MAX_DOMAIN_LEN)

    def _validate_path(self, path: str) -> None:
        _validate_str_field("path", path, self.MAX_PATH_LEN)

    def _validate_expires(self, expires: Optional[str]) -> None:
        if expires is not None:
            _validate_str_field("expires", expires, 100)

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
            # lastrowid is 0 on an upsert-update — look up the real id
            if not row_id:
                row_id = self._find_id(name, domain, path)
                if row_id < 0:
                    raise StorageError("Failed to locate upserted cookie row")
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
        raw_values = [v for k, v in response_headers.items() if k.lower() == "set-cookie"]
        set_cookies: List[str] = []
        for raw in raw_values:
            set_cookies.extend(self._split_combined_set_cookie_header(raw))
        self.update_from_set_cookie_headers(set_cookies, url)

    def update_from_set_cookie_headers(self, set_cookies: List[str], url: str) -> None:
        """Parse a list of raw ``Set-Cookie`` header values and upsert into the jar."""
        if not set_cookies:
            return

        try:
            default_domain = str(urls.url_metadata(url).get("hostname") or "")
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

    def to_httpx_cookie_records(self) -> List[Dict[str, str]]:
        """Return scoped cookie records for safe domain/path-aware httpx syncing."""
        rows = self.db.fetchall(
            "SELECT name, value, domain, path FROM cookies ORDER BY id"
        )
        return [
            {
                "name": str(row["name"]),
                "value": str(row["value"]),
                "domain": str(row["domain"] or ""),
                "path": str(row["path"] or "/"),
            }
            for row in rows
        ]

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
        name, _, value = first.partition("=")
        name = name.strip()
        value = value.strip()

        if not name:
            return

        domain = default_domain
        path = "/"
        secure = False
        http_only = False
        expires: Optional[str] = None

        for attr in parts[1:]:
            attr_key, _, attr_val = attr.partition("=")
            attr_key = attr_key.strip().lower()
            attr_val = attr_val.strip()
            if attr_key == "domain":
                domain = attr_val.lstrip(".")
            elif attr_key == "path":
                path = attr_val or "/"
            elif attr_key == "secure":
                secure = True
            elif attr_key == "httponly":
                http_only = True
            elif attr_key == "expires":
                expires = attr_val

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

    @staticmethod
    def _split_combined_set_cookie_header(raw: str) -> List[str]:
        """Split a combined Set-Cookie line into individual cookie values.

        Some adapters collapse repeated Set-Cookie headers into one comma-separated
        string. We split on cookie-boundary commas while preserving commas inside
        Expires=... attribute values.
        """
        if not raw:
            return []
        if "," not in raw:
            return [raw.strip()]

        parts: List[str] = []
        current: List[str] = []
        i = 0
        in_expires = False

        while i < len(raw):
            ch = raw[i]
            if raw[i : i + 8].lower() == "expires=":
                in_expires = True
            if ch == ";" and in_expires:
                in_expires = False
            if ch == "," and not in_expires:
                nxt = raw[i + 1 :]
                if "=" in nxt.split(";", 1)[0]:
                    piece = "".join(current).strip()
                    if piece:
                        parts.append(piece)
                    current = []
                    i += 1
                    continue
            current.append(ch)
            i += 1

        tail = "".join(current).strip()
        if tail:
            parts.append(tail)
        return parts

