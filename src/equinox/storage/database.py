"""Database connection and management"""

import sqlite3
import threading
from pathlib import Path
from typing import Optional, Any, List, Dict
from contextlib import contextmanager

from equinox.core.exceptions import StorageError


class Database:
    """SQLite database manager"""

    def __init__(self, db_path: str = "equinox.db"):
        """
        Initialize database

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = Path(db_path).resolve()
        self.lock = threading.Lock()
        self._init_schema()

    def _init_schema(self):
        """Initialize database schema"""
        schema_path = Path(__file__).parent / "schema.sql"
        try:
            with self.lock, self.get_connection() as conn:
                with open(schema_path, "r") as f:
                    conn.executescript(f.read())
                conn.commit()
        except Exception as e:
            raise StorageError(f"Failed to initialize database schema: {e}")

    @contextmanager
    def get_connection(self):
        """Get database connection context manager"""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def execute(self, query: str, params: tuple = ()) -> sqlite3.Cursor:
        """
        Execute a query

        Args:
            query: SQL query
            params: Query parameters

        Returns:
            Cursor object
        """
        with self.lock, self.get_connection() as conn:
            cursor = conn.execute(query, params)
            conn.commit()
            return cursor

    def fetchone(self, query: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
        """
        Fetch one row

        Args:
            query: SQL query
            params: Query parameters

        Returns:
            Row as dictionary or None
        """
        with self.lock, self.get_connection() as conn:
            cursor = conn.execute(query, params)
            row = cursor.fetchone()
            return dict(row) if row else None

    def fetchall(self, query: str, params: tuple = ()) -> List[Dict[str, Any]]:
        """
        Fetch all rows

        Args:
            query: SQL query
            params: Query parameters

        Returns:
            List of rows as dictionaries
        """
        with self.lock, self.get_connection() as conn:
            cursor = conn.execute(query, params)
            rows = cursor.fetchall()
            return [dict(row) for row in rows]

    def insert(self, query: str, params: tuple = ()) -> int:
        """
        Insert a row and return its ID

        Args:
            query: SQL insert query
            params: Query parameters

        Returns:
            Last inserted row ID
        """
        with self.lock, self.get_connection() as conn:
            cursor = conn.execute(query, params)
            conn.commit()
            return cursor.lastrowid

    def close(self):
        """Close database connection"""
        # Connections are closed automatically in context managers
        pass
