"""Secure SQLite database access with parameterized queries and thread safety."""
import re
import sqlite3
import threading
import logging
from pathlib import Path
from typing import Optional, Any, List, Dict, Tuple, Mapping, Union
from contextlib import contextmanager

from equinox.core.exceptions import StorageError, ValidationError, DuplicateError

logger = logging.getLogger(__name__)

__all__ = ["Database"]

# Type alias for SQL parameters — either positional (tuple / list) or named (Mapping).
_SqlParams = Union[Tuple[Any, ...], List[Any], Mapping[str, Any]]

_CONNECTION_TIMEOUT_SECONDS = 10.0


class QueryValidator:

    NAMED_PATTERN = re.compile(r":([A-Za-z_][A-Za-z0-9_]*)")

    @staticmethod
    def validate_placeholders(query: str, params) -> None:
        """
        Validate SQL placeholders for both positional (?) and named (:name) styles.
        """

        # Detect placeholder style
        has_positional = "?" in query
        has_named = bool(QueryValidator.NAMED_PATTERN.search(query))

        if has_positional and has_named:
            raise ValidationError("Cannot mix positional and named placeholders")

        if has_positional:
            QueryValidator._validate_positional(query, params)
        elif has_named:
            QueryValidator._validate_named(query, params)
        else:
            logger.debug("No placeholders found for query: %.100s", query)

    @staticmethod
    def _validate_positional(query: str, params: Tuple) -> None:
        """Validate positional '?' placeholders.

        Tracks single-quoted string literals using SQL's own escaping
        convention (doubled quotes ``''``), **not** backslash escaping.
        """
        in_string = False
        count = 0
        i = 0
        length = len(query)

        while i < length:
            char = query[i]

            if char == "'":
                if in_string:
                    # Check for escaped quote ('')
                    if i + 1 < length and query[i + 1] == "'":
                        i += 2  # skip both quotes
                        continue
                    in_string = False
                else:
                    in_string = True
            elif char == "?" and not in_string:
                count += 1

            i += 1

        if count != len(params):
            raise ValidationError(
                f"Expected {count} parameters but got {len(params)}"
            )

    @staticmethod
    def _validate_named(query: str, params: Mapping) -> None:
        """Validate named ':name' placeholders."""
        if not isinstance(params, Mapping):
            raise ValidationError("Named placeholders require a mapping (dict-like)")

        # Extract placeholder names outside string literals
        names = QueryValidator._extract_named_placeholders(query)

        missing = [n for n in names if n not in params]
        extra = [p for p in params.keys() if p not in names]

        if missing:
            raise ValidationError(f"Missing parameters for placeholders: {missing}")

        if extra:
            raise ValidationError(f"Extra parameters not used in query: {extra}")

    @staticmethod
    def _extract_named_placeholders(query: str):
        """Extract named placeholders outside string literals.

        Uses SQL's own escaping convention (doubled quotes ``''``),
        not backslash escaping.
        """
        in_string = False
        names = []

        i = 0
        length = len(query)
        while i < length:
            char = query[i]

            if char == "'":
                if in_string:
                    if i + 1 < length and query[i + 1] == "'":
                        i += 2  # skip escaped quote
                        continue
                    in_string = False
                else:
                    in_string = True

            elif char == ":" and not in_string:
                # Extract identifier
                match = QueryValidator.NAMED_PATTERN.match(query, i)
                if match:
                    names.append(match.group(1))
                    i = match.end()
                    continue

            i += 1

        return names


class Database:
    """Secure SQLite database manager.

    Features:
    - Automatic parameterized queries
    - SQL injection prevention
    - Thread-safe operations with a single persistent connection
    - Comprehensive error handling
    """

    MAX_QUERY_LENGTH = 10000
    MAX_PARAMS = 100

    def __init__(self, db_path: str = "equinox.db"):
        """Initialize database with path validation and schema migration.

        Args:
            db_path: Path to SQLite database file

        Raises:
            ValidationError: If db_path is invalid
            StorageError: If database initialization fails
        """
        self._conn: Optional[sqlite3.Connection] = None
        if not db_path or not isinstance(db_path, str):
            raise ValidationError("Database path must be a non-empty string")

        try:
            self.db_path = Path(db_path).resolve()
        except Exception as exc:
            raise ValidationError(f"Invalid database path: {exc}")

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()

        logger.info("Initializing database at %s", self.db_path)
        # Set file-level PRAGMAs (journal_mode, secure_delete) via a temp
        # connection before opening the persistent one.
        self._set_database_pragmas()
        # Open the long-lived connection used by all execute/fetch/insert calls.
        self._conn = self._new_connection()
        self._run_migrations()

    def _new_connection(self) -> sqlite3.Connection:
        """Create, configure, and return a new SQLite connection.

        Uses ``isolation_level=None`` (full manual / autocommit mode) so that
        Python's DB-API does not silently inject ``BEGIN`` statements, giving
        us explicit control over transaction boundaries.
        """
        conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            timeout=_CONNECTION_TIMEOUT_SECONDS,
            isolation_level=None,  # autocommit; transactions managed explicitly
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _set_database_pragmas(self) -> None:
        """Set persistent database-level PRAGMAs once at startup.

        ``journal_mode`` and ``secure_delete`` persist across connections,
        so they only need to be set once per database file.
        """
        try:
            conn = sqlite3.connect(self.db_path, timeout=_CONNECTION_TIMEOUT_SECONDS)
        except sqlite3.Error as exc:
            raise StorageError(f"Cannot open database to configure PRAGMAs: {exc}") from exc
        try:
            logger.debug("Setting database PRAGMAs: journal_mode=WAL, secure_delete=ON")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA secure_delete = ON")
            logger.debug("Database PRAGMAs configured successfully")
        except sqlite3.Error as exc:
            raise StorageError(f"Failed to set database PRAGMAs: {exc}") from exc
        finally:
            conn.close()

    def _run_migrations(self) -> None:
        """Run all pending schema migrations on startup.

        Uses the :class:`~equinox.storage.migrations.MigrationRunner` so the
        schema is always up-to-date without manual intervention.

        Raises:
            StorageError: If any migration fails.
        """
        from equinox.storage.migrations import MigrationRunner  # local import avoids circulars
        runner = MigrationRunner(self)
        try:
            version = runner.run()
            logger.info("Database schema at version %d", version)
        except Exception as exc:
            raise StorageError(f"Failed to run database migrations: {exc}") from exc

    def _init_schema(self) -> None:  # pragma: no cover
        """Deprecated — use _run_migrations."""
        self._run_migrations()

    @contextmanager
    def get_connection(self):
        """Context manager that yields the persistent database connection.

        The connection is long-lived and must **not** be closed by the caller.
        Acquires the database lock for the duration of the context so callers
        can safely read or write without racing other threads.

        Raises:
            StorageError: If the persistent connection is unavailable.
        """
        with self.lock:
            if getattr(self, "_conn", None) is None:
                self._conn = self._new_connection()
            yield self._conn

    @contextmanager
    def transaction(self):
        """Context manager for multi-statement transactions.

        All statements executed via the yielded helper run on the **same**
        persistent connection inside a single ``BEGIN … COMMIT`` block.
        If an exception occurs the transaction is rolled back.

        Usage::

            with db.transaction() as tx:
                tx.execute("INSERT INTO ...", (...))
                tx.execute("INSERT INTO ...", (...))
            # COMMIT happens automatically on clean exit

        Yields:
            A :class:`_TransactionHelper` with ``execute``, ``executemany``,
            ``fetchone``, ``fetchall``, and ``insert`` methods.
        """
        with self.lock:
            logger.debug("Starting database transaction")
            if getattr(self, "_conn", None) is None:
                self._conn = self._new_connection()
            conn = self._conn
            conn.execute("BEGIN")
            try:
                yield _TransactionHelper(conn)
                conn.execute("COMMIT")
                logger.debug("Transaction committed successfully")
            except Exception as exc:
                logger.warning("Transaction failed, rolling back: %s", exc, exc_info=False)
                try:
                    conn.execute("ROLLBACK")
                    logger.debug("Transaction rolled back")
                except Exception as rollback_exc:
                    logger.error("Failed to rollback transaction: %s", rollback_exc, exc_info=False)
                raise

    def _get_conn(self) -> sqlite3.Connection:
        """Return the live connection, or raise ``StorageError`` if the database is closed.

        Must be called while ``self.lock`` is held.
        """
        if getattr(self, "_conn", None) is None:
            raise StorageError("Database connection is closed")
        return self._conn

    def _validate_query(self, query: str, params: _SqlParams) -> None:
        """Validate query and parameters before execution.

        Args:
            query: SQL query
            params: Query parameters

        Raises:
            ValidationError: If validation fails
        """
        if not query or not isinstance(query, str):
            raise ValidationError("Query must be a non-empty string")
        if len(query) > self.MAX_QUERY_LENGTH:
            raise ValidationError(f"Query exceeds maximum length of {self.MAX_QUERY_LENGTH}")
        # Accept mapping parameters for named placeholders and sequence for
        # positional placeholders.
        has_named = bool(QueryValidator.NAMED_PATTERN.search(query))
        if has_named:
            if not isinstance(params, Mapping):
                raise ValidationError("Named placeholders require a mapping (dict-like)")
            if len(params) > self.MAX_PARAMS:
                raise ValidationError(f"Too many parameters (max: {self.MAX_PARAMS})")
        else:
            if not isinstance(params, (tuple, list)):
                raise ValidationError("Query parameters must be a tuple or list")
            if len(params) > self.MAX_PARAMS:
                raise ValidationError(f"Too many parameters (max: {self.MAX_PARAMS})")

        QueryValidator.validate_placeholders(query, params)

    def execute(self, query: str, params: _SqlParams = ()) -> sqlite3.Cursor:
        """Execute a query safely with validation.

        Args:
            query: SQL query with ? placeholders
            params: Query parameters (must match placeholder count)

        Returns:
            Cursor object

        Raises:
            ValidationError: If query/params are invalid
            StorageError: If execution fails or the connection is closed
        """
        self._validate_query(query, params)

        try:
            with self.lock:
                return self._get_conn().execute(query, params)
        except StorageError:
            raise
        except sqlite3.IntegrityError as exc:
            logger.error("Integrity error: %s", exc)
            msg = str(exc)
            if "UNIQUE" in msg.upper():
                raise DuplicateError(f"Database unique constraint violated: {exc}")
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

    def fetchone(self, query: str, params: _SqlParams = ()) -> Optional[Dict[str, Any]]:
        """Fetch one row safely.

        Args:
            query: SQL query with ? placeholders
            params: Query parameters

        Returns:
            Row as dictionary or None

        Raises:
            ValidationError: If query/params are invalid
            StorageError: If query fails or the connection is closed
        """
        self._validate_query(query, params)

        try:
            with self.lock:
                cursor = self._get_conn().execute(query, params)
                row = cursor.fetchone()
                return dict(row) if row else None
        except StorageError:
            raise
        except sqlite3.Error as exc:
            logger.error("Database error in fetchone: %s", exc)
            raise StorageError(f"Failed to fetch row: {exc}")

    def fetchall(self, query: str, params: _SqlParams = ()) -> List[Dict[str, Any]]:
        """Fetch all rows safely.

        Args:
            query: SQL query with ? placeholders
            params: Query parameters

        Returns:
            List of rows as dictionaries

        Raises:
            ValidationError: If query/params are invalid
            StorageError: If query fails or the connection is closed
        """
        self._validate_query(query, params)

        try:
            with self.lock:
                cursor = self._get_conn().execute(query, params)
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        except StorageError:
            raise
        except sqlite3.Error as exc:
            logger.error("Database error in fetchall: %s", exc)
            raise StorageError(f"Failed to fetch rows: {exc}")

    def insert(self, query: str, params: _SqlParams = ()) -> int:
        """Insert a row and return its ID safely.

        Args:
            query: SQL insert query with ? placeholders
            params: Query parameters

        Returns:
            Last inserted row ID

        Raises:
            ValidationError: If query/params are invalid
            StorageError: If insert fails or the connection is closed
        """
        self._validate_query(query, params)

        if not query.strip().upper().startswith('INSERT'):
            raise ValidationError("Query must be an INSERT statement")

        try:
            with self.lock:
                cursor = self._get_conn().execute(query, params)
                return cursor.lastrowid
        except StorageError:
            raise
        except sqlite3.IntegrityError as exc:
            logger.error("Integrity error during insert: %s", exc)
            msg = str(exc)
            if "UNIQUE" in msg.upper():
                raise DuplicateError(f"Failed to insert row (unique constraint): {exc}")
            raise StorageError(f"Failed to insert row (integrity constraint): {exc}")
        except sqlite3.Error as exc:
            logger.error("Database error during insert: %s", exc)
            raise StorageError(f"Failed to insert row: {exc}")
        except Exception as exc:
            logger.error("Unexpected error during insert: %s", exc)
            raise StorageError(f"Failed to insert row: {exc}")

    def close(self) -> None:
        """Close the persistent database connection."""
        with self.lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception as exc:
                    logger.debug("Error closing persistent connection: %s", exc, exc_info=False)
                finally:
                    self._conn = None

    def __del__(self) -> None:
        """Last-resort GC safety net — close the connection if the caller forgot.

        Suppresses the ``ResourceWarning: unclosed database`` that Python emits
        when a ``sqlite3.Connection`` is garbage-collected while still open.
        Must never raise, because exceptions from ``__del__`` are silently
        ignored by the interpreter (and may hide the real error).
        """
        try:
            # Avoid acquiring the lock here: __del__ may run during interpreter
            # shutdown when locks can deadlock.  Directly close if still open.
            conn = getattr(self, "_conn", None)
            if conn is not None:
                conn.close()
                self._conn = None
        except Exception:  # noqa: BLE001
            pass

    # ── Context-manager support ───────────────────────────────────────────────

    def __enter__(self) -> "Database":
        """Return *self* so ``with Database(...) as db:`` works."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Close the connection on context exit (clean or exceptional)."""
        self.close()


class _TransactionHelper:
    """Thin wrapper around a ``sqlite3.Connection`` for use inside
    :meth:`Database.transaction`.

    All methods execute on the **same** connection so they participate in
    the enclosing transaction.  Basic query validation mirrors
    :meth:`Database._validate_query` to prevent accidental SQL injection
    even within a transaction context.
    """

    __slots__ = ("_conn",)

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    @staticmethod
    def _validate(query: str, params: "_SqlParams") -> None:
        """Validate query and parameters before execution."""
        if not query or not isinstance(query, str):
            raise ValidationError("Query must be a non-empty string")
        if len(query) > Database.MAX_QUERY_LENGTH:
            raise ValidationError(
                f"Query exceeds maximum length of {Database.MAX_QUERY_LENGTH}"
            )
        if len(params) > Database.MAX_PARAMS:
            raise ValidationError(f"Too many parameters (max: {Database.MAX_PARAMS})")
        QueryValidator.validate_placeholders(query, params)

    def execute(self, query: str, params: "_SqlParams" = ()) -> sqlite3.Cursor:
        """Execute a query and return the cursor."""
        self._validate(query, params)
        return self._conn.execute(query, params)

    def executemany(self, query: str, seq_of_params) -> sqlite3.Cursor:
        """Execute a query against all parameter sequences."""
        if not query or not isinstance(query, str):
            raise ValidationError("Query must be a non-empty string")
        return self._conn.executemany(query, seq_of_params)

    def fetchone(self, query: str, params: "_SqlParams" = ()) -> Optional[Dict[str, Any]]:
        """Execute a query and return a single row as a dict, or None."""
        self._validate(query, params)
        cursor = self._conn.execute(query, params)
        row = cursor.fetchone()
        return dict(row) if row else None

    def fetchall(self, query: str, params: "_SqlParams" = ()) -> List[Dict[str, Any]]:
        """Execute a query and return all rows as dicts."""
        self._validate(query, params)
        cursor = self._conn.execute(query, params)
        return [dict(r) for r in cursor.fetchall()]

    def insert(self, query: str, params: "_SqlParams" = ()) -> int:
        """Execute an INSERT and return the last row id."""
        self._validate(query, params)
        if not query.strip().upper().startswith('INSERT'):
            raise ValidationError("Query must be an INSERT statement")
        cursor = self._conn.execute(query, params)
        return cursor.lastrowid

