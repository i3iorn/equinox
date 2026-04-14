"""Secure SQLite database access with parameterized queries and thread safety."""
from __future__ import annotations

import re
import sqlite3
import threading
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional, Tuple, Union

from equinox.core.exceptions import StorageError, ValidationError, DuplicateError

logger = logging.getLogger(__name__)

__all__ = ["Database"]

# ---------------------------------------------------------------------------
# Type aliases and module-level constants
# ---------------------------------------------------------------------------

#: Type alias for SQL parameters — positional (tuple/list) or named (Mapping).
_SqlParams = Union[Tuple[Any, ...], List[Any], Mapping[str, Any]]

_CONNECTION_TIMEOUT_SECONDS = 10.0

# Compiled pattern for named SQL placeholders (e.g. ``:user_id``).
_NAMED_PARAM_RE = re.compile(r":([A-Za-z_][A-Za-z0-9_]*)")

# Shared limits — referenced by both Database and _TransactionHelper via the
# module-level constants so a single change updates both classes.
_MAX_QUERY_LENGTH = 10_000
_MAX_PARAMS       = 100


# ---------------------------------------------------------------------------
# SQL validation helpers
# ---------------------------------------------------------------------------

def _outside_string(query: str) -> Iterator[Tuple[int, str]]:
    """Yield ``(index, char)`` for every character outside a single-quoted
    SQL string literal.  Uses SQL's ``''`` escape convention, not backslash.
    """
    in_string = False
    i = 0
    n = len(query)
    while i < n:
        c = query[i]
        if c == "'":
            if in_string and i + 1 < n and query[i + 1] == "'":
                i += 2   # skip escaped ''
                continue
            in_string = not in_string
        elif not in_string:
            yield i, c
        i += 1


def _extract_named_placeholders(query: str) -> List[str]:
    """Return named placeholder identifiers found outside string literals."""
    names: List[str] = []
    skip_until = -1
    for i, c in _outside_string(query):
        if i < skip_until:
            continue
        if c == ":":
            m = _NAMED_PARAM_RE.match(query, i)
            if m:
                names.append(m.group(1))
                skip_until = m.end()
    return names


def _validate_placeholders(query: str, params: _SqlParams) -> None:
    """Validate that SQL placeholders in *query* match the supplied *params*.

    Raises:
        ValidationError: On style mixing, count mismatch, or missing/extra keys.
    """
    has_positional = "?" in query
    has_named      = bool(_NAMED_PARAM_RE.search(query))

    if has_positional and has_named:
        raise ValidationError("Cannot mix positional and named placeholders")

    if has_positional:
        count = sum(1 for _, c in _outside_string(query) if c == "?")
        if count != len(params):
            raise ValidationError(
                f"Expected {count} parameters but got {len(params)}"
            )
    elif has_named:
        if not isinstance(params, Mapping):
            raise ValidationError("Named placeholders require a mapping (dict-like)")
        names   = _extract_named_placeholders(query)
        missing = [n for n in names if n not in params]
        extra   = [p for p in params if p not in names]
        if missing:
            raise ValidationError(f"Missing parameters for placeholders: {missing}")
        if extra:
            raise ValidationError(f"Extra parameters not used in query: {extra}")
    else:
        logger.debug("No placeholders found for query: %.100s", query)


def _validate_sql(query: str, params: _SqlParams) -> None:
    """Validate *query* string and *params* before any execution.

    Shared by :class:`Database` and :class:`_TransactionHelper` so the rules
    are defined in exactly one place.

    Raises:
        ValidationError: If any check fails.
    """
    if not query or not isinstance(query, str):
        raise ValidationError("Query must be a non-empty string")
    if len(query) > _MAX_QUERY_LENGTH:
        raise ValidationError(
            f"Query exceeds maximum length of {_MAX_QUERY_LENGTH}"
        )
    if isinstance(params, Mapping):
        if len(params) > _MAX_PARAMS:
            raise ValidationError(f"Too many parameters (max: {_MAX_PARAMS})")
    elif isinstance(params, (tuple, list)):
        if len(params) > _MAX_PARAMS:
            raise ValidationError(f"Too many parameters (max: {_MAX_PARAMS})")
    else:
        raise ValidationError("Query parameters must be a tuple, list, or mapping")
    _validate_placeholders(query, params)


def _run_sqlite(fn, query: str, params: _SqlParams) -> sqlite3.Cursor:
    """Call *fn(query, params)* and map ``sqlite3`` errors to app exceptions.

    Centralises the error-translation logic that would otherwise be duplicated
    in every ``execute`` / ``fetchone`` / ``fetchall`` / ``insert`` method.

    Raises:
        DuplicateError: On UNIQUE constraint violation.
        StorageError: On any other SQLite or unexpected failure.
    """
    try:
        return fn(query, params)
    except StorageError:
        raise
    except sqlite3.IntegrityError as exc:
        logger.error("Integrity constraint violated: %s", exc)
        if "UNIQUE" in str(exc).upper():
            raise DuplicateError(f"Unique constraint violated: {exc}")
        raise StorageError(f"Database integrity error: {exc}")
    except sqlite3.OperationalError as exc:
        logger.error("Operational error: %s", exc)
        raise StorageError(f"Database operational error: {exc}")
    except sqlite3.Error as exc:
        logger.error("Database error: %s", exc)
        raise StorageError(f"Database error: {exc}")
    except Exception as exc:
        logger.error("Unexpected error during query execution: %s", exc)
        raise StorageError(f"Query execution failed: {exc}")


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

class Database:
    """Secure SQLite database manager.

    - Parameterized queries only — no string formatting in SQL.
    - SQL injection prevention via placeholder validation.
    - Thread-safe with a single persistent connection and a reentrant lock.
    - WAL journal mode for concurrent reader/writer access.
    - Automatic schema migration on ``__init__``.
    """

    # Re-exported for backward compatibility; canonical values are module-level.
    MAX_QUERY_LENGTH = _MAX_QUERY_LENGTH
    MAX_PARAMS       = _MAX_PARAMS

    def __init__(self, db_path: str = "equinox.db") -> None:
        """Open (or create) the database and run pending schema migrations.

        Args:
            db_path: Path to the SQLite database file.

        Raises:
            ValidationError: If *db_path* is invalid.
            StorageError: If the database cannot be opened or migrated.
        """
        self._conn: Optional[sqlite3.Connection] = None

        if not db_path or not isinstance(db_path, str):
            raise ValidationError("Database path must be a non-empty string")
        try:
            self.db_path = Path(db_path).resolve()
        except Exception as exc:
            raise ValidationError(f"Invalid database path: {exc}")

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

        logger.info("Initializing database at %s", self.db_path)
        # WAL mode and secure_delete are database-level settings that persist
        # across connections, so configure them once on a temporary connection
        # before opening the long-lived one.
        self._configure_persistent_pragmas()
        self._conn = self._new_connection()
        self._run_migrations()

    # ── Connection management ─────────────────────────────────────────────────

    def _new_connection(self) -> sqlite3.Connection:
        """Create, configure, and return a new SQLite connection.

        ``isolation_level=None`` (autocommit) prevents Python's DB-API from
        injecting implicit ``BEGIN`` statements; transactions are fully explicit.
        """
        conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            timeout=_CONNECTION_TIMEOUT_SECONDS,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _configure_persistent_pragmas(self) -> None:
        """Set database-level PRAGMAs that persist across connections.

        ``journal_mode=WAL`` and ``secure_delete=ON`` only need to be applied
        once per database file, so a short-lived connection is used here rather
        than waiting for the persistent one.
        """
        try:
            conn = sqlite3.connect(self.db_path, timeout=_CONNECTION_TIMEOUT_SECONDS)
        except sqlite3.Error as exc:
            raise StorageError(
                f"Cannot open database to configure PRAGMAs: {exc}"
            ) from exc
        try:
            logger.debug("Configuring PRAGMAs: journal_mode=WAL, secure_delete=ON")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA secure_delete = ON")
        except sqlite3.Error as exc:
            raise StorageError(f"Failed to configure database PRAGMAs: {exc}") from exc
        finally:
            conn.close()

    def _run_migrations(self) -> None:
        """Run all pending schema migrations via :class:`MigrationRunner`.

        Triggered automatically in ``__init__`` — no manual call needed.

        Raises:
            StorageError: If any migration fails.
        """
        from equinox.storage.migrations import MigrationRunner  # avoid circular import
        try:
            version = MigrationRunner(self).run()
            logger.info("Database schema at version %d", version)
        except Exception as exc:
            raise StorageError(f"Failed to run database migrations: {exc}") from exc

    def _require_conn(self) -> sqlite3.Connection:
        """Return the live connection, or raise ``StorageError`` if closed.

        Must be called while ``self._lock`` is held.
        """
        conn = getattr(self, "_conn", None)
        if conn is None:
            raise StorageError("Database connection is closed")
        return conn

    # ── Backward-compatible public attribute ──────────────────────────────────

    @property
    def lock(self) -> threading.Lock:
        """The database mutex.

        Exposed for callers that need to hold the lock across multiple
        operations (e.g. migration runners).  Prefer :meth:`transaction`
        for all normal multi-statement work.
        """
        return self._lock

    # ── Context managers ──────────────────────────────────────────────────────

    @contextmanager
    def get_connection(self):
        """Yield the persistent connection under the database lock.

        Low-level escape hatch — prefer the typed helpers
        (``execute``, ``fetchone``, ``fetchall``, ``insert``, ``transaction``)
        for all normal usage.  The connection **must not** be closed by the caller.

        Raises:
            StorageError: If the connection is unavailable.
        """
        with self._lock:
            yield self._require_conn()

    @contextmanager
    def transaction(self):
        """Context manager for multi-statement atomic transactions.

        All statements executed on the yielded :class:`_TransactionHelper` run
        inside a single ``BEGIN … COMMIT`` block on the persistent connection.
        Any exception triggers an automatic ``ROLLBACK``.

        Usage::

            with db.transaction() as tx:
                tx.execute("INSERT INTO ...", (...))
                tx.execute("UPDATE  ...", (...))
            # COMMIT on clean exit; ROLLBACK on exception

        Yields:
            :class:`_TransactionHelper`
        """
        with self._lock:
            conn = self._require_conn()
            logger.debug("Starting database transaction")
            conn.execute("BEGIN")
            try:
                yield _TransactionHelper(conn)
                conn.execute("COMMIT")
                logger.debug("Transaction committed")
            except Exception as exc:
                logger.warning("Transaction rolling back: %s", exc, exc_info=False)
                try:
                    conn.execute("ROLLBACK")
                    logger.debug("Transaction rolled back")
                except Exception as rb_exc:
                    logger.error("Rollback failed: %s", rb_exc, exc_info=False)
                raise

    # ── Query helpers ─────────────────────────────────────────────────────────

    def execute(self, query: str, params: _SqlParams = ()) -> sqlite3.Cursor:
        """Execute a write (or DDL) statement and return the cursor.

        Raises:
            ValidationError: If *query* or *params* are invalid.
            DuplicateError: On UNIQUE constraint violation.
            StorageError: On any other failure.
        """
        _validate_sql(query, params)
        with self._lock:
            return _run_sqlite(self._require_conn().execute, query, params)

    def fetchone(self, query: str, params: _SqlParams = ()) -> Optional[Dict[str, Any]]:
        """Fetch a single row and return it as a ``dict``, or ``None``.

        Raises:
            ValidationError: If *query* or *params* are invalid.
            StorageError: On failure.
        """
        _validate_sql(query, params)
        with self._lock:
            cursor = _run_sqlite(self._require_conn().execute, query, params)
            row = cursor.fetchone()
            return dict(row) if row else None

    def fetchall(self, query: str, params: _SqlParams = ()) -> List[Dict[str, Any]]:
        """Fetch all rows and return them as a ``list`` of ``dict``.

        Raises:
            ValidationError: If *query* or *params* are invalid.
            StorageError: On failure.
        """
        _validate_sql(query, params)
        with self._lock:
            cursor = _run_sqlite(self._require_conn().execute, query, params)
            return [dict(row) for row in cursor.fetchall()]

    def insert(self, query: str, params: _SqlParams = ()) -> int:
        """Execute an INSERT and return the new row's ID.

        Raises:
            ValidationError: If *query* or *params* are invalid, or the
                statement is not an INSERT.
            DuplicateError: On UNIQUE constraint violation.
            StorageError: On any other failure.
        """
        _validate_sql(query, params)
        if not query.lstrip().upper().startswith("INSERT"):
            raise ValidationError("Query must be an INSERT statement")
        with self._lock:
            cursor = _run_sqlite(self._require_conn().execute, query, params)
            return cursor.lastrowid

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def close(self) -> None:
        """Close the persistent database connection."""
        with self._lock:
            conn = self._conn
            if conn is not None:
                try:
                    conn.close()
                except Exception as exc:
                    logger.debug("Error closing connection: %s", exc, exc_info=False)
                finally:
                    self._conn = None

    def __del__(self) -> None:
        """GC safety net — suppress ``ResourceWarning: unclosed database``.

        Must never raise.  The lock is intentionally bypassed because ``__del__``
        may run during interpreter shutdown when locks can deadlock.
        """
        try:
            conn = getattr(self, "_conn", None)
            if conn is not None:
                conn.close()
                self._conn = None
        except Exception:  # noqa: BLE001
            pass

    def __enter__(self) -> "Database":
        """Return *self* so ``with Database(...) as db:`` works."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Close on context exit (clean or exceptional)."""
        self.close()


# ---------------------------------------------------------------------------
# _TransactionHelper
# ---------------------------------------------------------------------------

class _TransactionHelper:
    """Thin wrapper around a ``sqlite3.Connection`` for use inside
    :meth:`Database.transaction`.

    All methods execute on the same connection and therefore participate in
    the enclosing ``BEGIN … COMMIT`` block.  Validation mirrors
    :func:`_validate_sql` so injection protection is always active.
    """

    __slots__ = ("_conn",)

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def execute(self, query: str, params: _SqlParams = ()) -> sqlite3.Cursor:
        """Execute a statement and return the cursor."""
        _validate_sql(query, params)
        return _run_sqlite(self._conn.execute, query, params)

    def executemany(self, query: str, seq_of_params) -> sqlite3.Cursor:
        """Execute a statement against each item in *seq_of_params*."""
        if not query or not isinstance(query, str):
            raise ValidationError("Query must be a non-empty string")
        if len(query) > _MAX_QUERY_LENGTH:
            raise ValidationError(
                f"Query exceeds maximum length of {_MAX_QUERY_LENGTH}"
            )
        return self._conn.executemany(query, seq_of_params)

    def fetchone(self, query: str, params: _SqlParams = ()) -> Optional[Dict[str, Any]]:
        """Execute a query and return a single row as a ``dict``, or ``None``."""
        _validate_sql(query, params)
        cursor = _run_sqlite(self._conn.execute, query, params)
        row = cursor.fetchone()
        return dict(row) if row else None

    def fetchall(self, query: str, params: _SqlParams = ()) -> List[Dict[str, Any]]:
        """Execute a query and return all rows as ``dict``."""
        _validate_sql(query, params)
        cursor = _run_sqlite(self._conn.execute, query, params)
        return [dict(r) for r in cursor.fetchall()]

    def insert(self, query: str, params: _SqlParams = ()) -> int:
        """Execute an INSERT and return the last row id.

        Raises:
            ValidationError: If the statement is not an INSERT.
            DuplicateError: On UNIQUE constraint violation.
        """
        _validate_sql(query, params)
        if not query.lstrip().upper().startswith("INSERT"):
            raise ValidationError("Query must be an INSERT statement")
        cursor = _run_sqlite(self._conn.execute, query, params)
        return cursor.lastrowid

