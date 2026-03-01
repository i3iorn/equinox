"""Secure SQLite database access with parameterized queries and thread safety."""

import sqlite3
import threading
import logging
from pathlib import Path
from typing import Optional, Any, List, Dict, Tuple
from contextlib import contextmanager

from equinox.core.exceptions import StorageError, ValidationError
from equinox.core.validation import Validator

logger = logging.getLogger(__name__)

_CONNECTION_TIMEOUT_SECONDS = 10.0


class Database:
    """Secure SQLite database manager.

    Features:
    - Automatic parameterized queries
    - SQL injection prevention
    - Thread-safe operations
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
        if not db_path or not isinstance(db_path, str):
            raise ValidationError("Database path must be a non-empty string")

        try:
            self.db_path = Path(db_path).resolve()
        except Exception as exc:
            raise ValidationError(f"Invalid database path: {exc}")

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()

        logger.info(f"Initializing database at {self.db_path}")
        self._run_migrations()

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
        """Context manager that yields a configured SQLite connection.

        Raises:
            StorageError: If connection fails
        """
        try:
            conn = sqlite3.connect(
                self.db_path,
                check_same_thread=False,
                timeout=_CONNECTION_TIMEOUT_SECONDS,
                isolation_level='DEFERRED'
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA secure_delete = ON")
            yield conn
        except sqlite3.Error as exc:
            logger.error(f"Database connection error: {exc}")
            raise StorageError(f"Database connection failed: {exc}")
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _validate_query(self, query: str, params: Tuple) -> None:
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
        if not isinstance(params, (tuple, list)):
            raise ValidationError("Query parameters must be a tuple or list")
        if len(params) > self.MAX_PARAMS:
            raise ValidationError(f"Too many parameters (max: {self.MAX_PARAMS})")

        placeholder_count = query.count('?')
        if placeholder_count != len(params):
            raise ValidationError(
                f"Parameter count mismatch: query has {placeholder_count} placeholders "
                f"but {len(params)} parameters provided"
            )

    def execute(self, query: str, params: Tuple = ()) -> sqlite3.Cursor:
        """Execute a query safely with validation.

        Args:
            query: SQL query with ? placeholders
            params: Query parameters (must match placeholder count)

        Returns:
            Cursor object

        Raises:
            ValidationError: If query/params are invalid
            StorageError: If execution fails
        """
        self._validate_query(query, params)

        try:
            with self.lock, self.get_connection() as conn:
                cursor = conn.execute(query, params)
                conn.commit()
                return cursor
        except sqlite3.IntegrityError as exc:
            logger.error(f"Integrity error: {exc}")
            raise StorageError(f"Database integrity error: {exc}")
        except sqlite3.OperationalError as exc:
            logger.error(f"Operational error: {exc}")
            raise StorageError(f"Database operational error: {exc}")
        except sqlite3.Error as exc:
            logger.error(f"Database error: {exc}")
            raise StorageError(f"Database error: {exc}")
        except Exception as exc:
            logger.error(f"Unexpected error during query execution: {exc}")
            raise StorageError(f"Query execution failed: {exc}")

    def fetchone(self, query: str, params: Tuple = ()) -> Optional[Dict[str, Any]]:
        """Fetch one row safely.

        Args:
            query: SQL query with ? placeholders
            params: Query parameters

        Returns:
            Row as dictionary or None

        Raises:
            ValidationError: If query/params are invalid
            StorageError: If query fails
        """
        self._validate_query(query, params)

        try:
            with self.lock, self.get_connection() as conn:
                cursor = conn.execute(query, params)
                row = cursor.fetchone()
                return dict(row) if row else None
        except sqlite3.Error as exc:
            logger.error(f"Database error in fetchone: {exc}")
            raise StorageError(f"Failed to fetch row: {exc}")

    def fetchall(self, query: str, params: Tuple = ()) -> List[Dict[str, Any]]:
        """Fetch all rows safely.

        Args:
            query: SQL query with ? placeholders
            params: Query parameters

        Returns:
            List of rows as dictionaries

        Raises:
            ValidationError: If query/params are invalid
            StorageError: If query fails
        """
        self._validate_query(query, params)

        try:
            with self.lock, self.get_connection() as conn:
                cursor = conn.execute(query, params)
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        except sqlite3.Error as exc:
            logger.error(f"Database error in fetchall: {exc}")
            raise StorageError(f"Failed to fetch rows: {exc}")

    def insert(self, query: str, params: Tuple = ()) -> int:
        """Insert a row and return its ID safely.

        Args:
            query: SQL insert query with ? placeholders
            params: Query parameters

        Returns:
            Last inserted row ID

        Raises:
            ValidationError: If query/params are invalid
            StorageError: If insert fails
        """
        self._validate_query(query, params)

        if not query.strip().upper().startswith('INSERT'):
            raise ValidationError("Query must be an INSERT statement")

        try:
            with self.lock, self.get_connection() as conn:
                cursor = conn.execute(query, params)
                conn.commit()
                return cursor.lastrowid
        except sqlite3.IntegrityError as exc:
            logger.error(f"Integrity error during insert: {exc}")
            raise StorageError(f"Failed to insert row (integrity constraint): {exc}")
        except sqlite3.Error as exc:
            logger.error(f"Database error during insert: {exc}")
            raise StorageError(f"Failed to insert row: {exc}")
        except Exception as exc:
            logger.error(f"Unexpected error during insert: {exc}")
            raise StorageError(f"Failed to insert row: {exc}")

    def close(self):
        """No-op — connections are closed automatically in context managers."""
        pass
