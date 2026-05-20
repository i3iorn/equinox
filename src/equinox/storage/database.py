"""Secure SQLite database access with parameterized queries and thread safety."""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Union

from equinox.core.exceptions import DuplicateError, StorageError, ValidationError

logger = logging.getLogger(__name__)

__all__ = ["Database"]

# ---------------------------------------------------------------------------
# Type aliases and module-level constants
# ---------------------------------------------------------------------------

#: Type alias for SQL parameters — positional (tuple/list) or named (Mapping).
_SqlParams = Union[tuple[Any, ...], list[Any], Mapping[str, Any]]

_CONNECTION_TIMEOUT_SECONDS = 10.0

# Compiled pattern for named SQL placeholders (e.g. ``:user_id``).
_NAMED_PARAM_RE = re.compile(r":([A-Za-z_][A-Za-z0-9_]*)")

# Shared limits — referenced by both Database and _TransactionHelper via the
# module-level constants so a single change updates both classes.
_MAX_QUERY_LENGTH = 10_000
_MAX_PARAMS = 100


# ---------------------------------------------------------------------------
# SQL validation helpers
# ---------------------------------------------------------------------------


def _outside_string(query: str) -> Iterator[tuple[int, str]]:
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
                i += 2  # skip escaped ''
                continue
            in_string = not in_string
        elif not in_string:
            yield i, c
        i += 1


def _extract_named_placeholders(query: str) -> list[str]:
    """Return named placeholder identifiers found outside string literals."""
    names: list[str] = []
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
    has_positional = any(c == "?" for _, c in _outside_string(query))
    has_named_names = _extract_named_placeholders(query)
    has_named = bool(has_named_names)

    if has_positional and has_named:
        raise ValidationError("Cannot mix positional and named placeholders")

    if has_positional:
        if isinstance(params, Mapping):
            raise ValidationError("Positional placeholders require tuple/list parameters")
        count = sum(1 for _, c in _outside_string(query) if c == "?")
        if count != len(params):
            raise ValidationError(f"Expected {count} parameters but got {len(params)}")
    elif has_named:
        if not isinstance(params, Mapping):
            raise ValidationError("Named placeholders require a mapping (dict-like)")
        names = has_named_names
        missing = [n for n in names if n not in params]
        extra = [p for p in params if p not in names]
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
        raise ValidationError(f"Query exceeds maximum length of {_MAX_QUERY_LENGTH}")
    if isinstance(params, Mapping):
        if len(params) > _MAX_PARAMS:
            raise ValidationError(f"Too many parameters (max: {_MAX_PARAMS})")
    elif isinstance(params, (tuple, list)):
        if len(params) > _MAX_PARAMS:
            raise ValidationError(f"Too many parameters (max: {_MAX_PARAMS})")
    else:
        raise ValidationError("Query parameters must be a tuple, list, or mapping")
    _validate_placeholders(query, params)


def _run_sqlite(
    fn: Callable[..., sqlite3.Cursor], query: str, params: _SqlParams
) -> sqlite3.Cursor:
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
    MAX_PARAMS = _MAX_PARAMS

    def __init__(self, db_path: str = "equinox.db") -> None:
        """Open (or create) the database and run pending schema migrations.

        Args:
            db_path: Path to the SQLite database file.

        Raises:
            ValidationError: If *db_path* is invalid.
            StorageError: If the database cannot be opened or migrated.
        """
        self._conn: sqlite3.Connection | None = None

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
        self._conn = self._initialize_connection_with_retry()
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

    def _initialize_connection_with_retry(self) -> sqlite3.Connection:
        """Establish database connection with transient lock retry.

        When the database is locked by another connection (e.g., WAL checkpoint),
        retry with exponential backoff instead of failing immediately.

        See Also:
            https://sqlite.org/lang_vacuum.html#how_vacuum_works
        """
        max_retries = 3
        for attempt in range(max_retries):
            try:
                conn = self._new_connection()
                logger.info(
                    "Database connection established at %s (attempt %d/%d)",
                    self.db_path,
                    attempt + 1,
                    max_retries,
                )
                return conn
            except sqlite3.OperationalError as exc:
                error_msg = str(exc).lower()
                is_locked = "locked" in error_msg or "busy" in error_msg

                if is_locked and attempt < max_retries - 1:
                    # Transient lock; retry with backoff
                    wait = (2**attempt) * 0.1  # 0.1, 0.2, 0.4 seconds
                    logger.warning(
                        "Database locked (attempt %d/%d), retrying in %.1fs",
                        attempt + 1,
                        max_retries,
                        wait,
                    )
                    import time

                    time.sleep(wait)
                else:
                    # Final attempt or non-lock error
                    logger.error(
                        "Cannot open database at %s: %s",
                        self.db_path,
                        str(exc),
                        extra={
                            "path": str(self.db_path),
                            "is_locked": is_locked,
                            "attempts": attempt + 1,
                        },
                    )
                    raise StorageError(
                        f"Cannot open database: {str(exc)}",
                        details={
                            "path": str(self.db_path),
                            "error": str(exc),
                            "is_locked": is_locked,
                        },
                        hint_key="connection",
                    ) from exc
            except Exception as exc:
                logger.error(
                    "Unexpected error opening database at %s: %s",
                    self.db_path,
                    str(exc),
                    exc_info=True,
                )
                raise StorageError(
                    f"Cannot open database: {str(exc)}",
                    details={"path": str(self.db_path), "error": str(exc)},
                ) from exc

        raise StorageError("Cannot open database after retries")

    def _configure_persistent_pragmas(self) -> None:
        """Set database-level PRAGMAs that persist across connections.

        ``journal_mode=WAL`` and ``secure_delete=ON`` only need to be applied
        once per database file, so a short-lived connection is used here rather
        than waiting for the persistent one.
        """
        try:
            # Use string path to ensure compatibility across Python versions
            # and because sqlite3.connect expects a path-like object.
            conn = sqlite3.connect(str(self.db_path), timeout=_CONNECTION_TIMEOUT_SECONDS)
        except sqlite3.Error as exc:
            raise StorageError(f"Cannot open database to configure PRAGMAs: {exc}") from exc
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
    def get_connection(self) -> Iterator[sqlite3.Connection]:
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
    def transaction(self) -> Iterator["_TransactionHelper"]:
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

    def fetchone(self, query: str, params: _SqlParams = ()) -> dict[str, Any] | None:
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

    def fetchall(self, query: str, params: _SqlParams = ()) -> list[dict[str, Any]]:
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
            row_id = cursor.lastrowid
            if row_id is None:
                raise StorageError("INSERT did not return a row id")
            return int(row_id)

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

    def __enter__(self) -> Database:
        """Return *self* so ``with Database(...) as db:`` works."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
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

    def executemany(
        self,
        query: str,
        seq_of_params: list[tuple[Any, ...]] | tuple[tuple[Any, ...], ...],
    ) -> sqlite3.Cursor:
        """Execute a statement against each item in *seq_of_params*.

        Applies the same query-length validation as :meth:`execute` and routes
        errors through the shared :func:`_run_sqlite` mapper so callers always
        receive ``StorageError`` / ``DuplicateError`` — never raw
        ``sqlite3.Error`` — consistent with every other helper on this class.

        Raises:
            ValidationError: If *query* is invalid.
            DuplicateError: On UNIQUE constraint violation.
            StorageError: On any other SQLite failure.
        """
        if not query or not isinstance(query, str):
            raise ValidationError("Query must be a non-empty string")
        if len(query) > _MAX_QUERY_LENGTH:
            raise ValidationError(f"Query exceeds maximum length of {_MAX_QUERY_LENGTH}")
        return _run_sqlite(self._conn.executemany, query, seq_of_params)

    def fetchone(self, query: str, params: _SqlParams = ()) -> dict[str, Any] | None:
        """Execute a query and return a single row as a ``dict``, or ``None``."""
        _validate_sql(query, params)
        cursor = _run_sqlite(self._conn.execute, query, params)
        row = cursor.fetchone()
        return dict(row) if row else None

    def fetchall(self, query: str, params: _SqlParams = ()) -> list[dict[str, Any]]:
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
        row_id = cursor.lastrowid
        if row_id is None:
            raise StorageError("INSERT did not return a row id")
        return int(row_id)
