"""Database migration system for Equinox.

Migrations are versioned, ordered SQL scripts applied exactly once to an
existing database.  The current schema version is tracked in a ``schema_version``
table so the system is safe to run on every startup.

Usage
-----
    from equinox.storage.database import Database
    from equinox.storage.migrations import MigrationRunner

    db = Database("equinox.db")
    runner = MigrationRunner(db)
    runner.run()           # applies any pending migrations
    print(runner.version)  # current version after run

Adding a migration
------------------
1. Add a new :class:`Migration` entry to :data:`MIGRATIONS` with the next
   incremental version number.
2. Write idempotent SQL (use ``IF NOT EXISTS``, ``IF NOT EXISTS`` column checks
   via ALTER TABLE ... where supported, etc.).
3. The runner guarantees each migration runs exactly once per database file.
"""

import logging
import sqlite3
from dataclasses import dataclass
from typing import List

from equinox.core.exceptions import StorageError

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Migration registry
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Migration:
    """A single versioned database migration."""

    version: int
    description: str
    sql: str


# All migrations in strict version order.  Never reorder or delete entries.
MIGRATIONS: List[Migration] = [
    Migration(
        version=1,
        description="Initial schema",
        sql="""
-- Collections
CREATE TABLE IF NOT EXISTS collections (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE,
    description TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Requests
CREATE TABLE IF NOT EXISTS requests (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    collection_id INTEGER,
    name          TEXT    NOT NULL,
    description   TEXT,
    method        TEXT    NOT NULL,
    url           TEXT    NOT NULL,
    headers       TEXT,
    params        TEXT,
    body          TEXT,
    auth_type     TEXT,
    auth_data     TEXT,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (collection_id) REFERENCES collections(id) ON DELETE CASCADE
);

-- History
CREATE TABLE IF NOT EXISTS history (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id       INTEGER,
    method           TEXT NOT NULL,
    url              TEXT NOT NULL,
    status_code      INTEGER,
    reason           TEXT,
    request_headers  TEXT,
    request_body     TEXT,
    response_headers TEXT,
    response_body    BLOB,
    elapsed          REAL,
    error            TEXT,
    executed_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (request_id) REFERENCES requests(id) ON DELETE SET NULL
);

-- Environments
CREATE TABLE IF NOT EXISTS environments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE,
    description TEXT,
    variables   TEXT    NOT NULL,
    is_active   INTEGER DEFAULT 0,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Collection variables
CREATE TABLE IF NOT EXISTS collection_variables (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    collection_id INTEGER NOT NULL,
    key           TEXT    NOT NULL,
    value         TEXT    NOT NULL,
    description   TEXT,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (collection_id) REFERENCES collections(id) ON DELETE CASCADE,
    UNIQUE(collection_id, key)
);

-- Variable groups
CREATE TABLE IF NOT EXISTS variable_groups (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE,
    description TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Variable group items
CREATE TABLE IF NOT EXISTS variable_group_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id    INTEGER NOT NULL,
    key         TEXT    NOT NULL,
    value       TEXT    NOT NULL,
    description TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (group_id) REFERENCES variable_groups(id) ON DELETE CASCADE,
    UNIQUE(group_id, key)
);

-- Collection ↔ variable-group many-to-many
CREATE TABLE IF NOT EXISTS collection_variable_groups (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    collection_id INTEGER NOT NULL,
    group_id      INTEGER NOT NULL,
    priority      INTEGER DEFAULT 0,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (collection_id) REFERENCES collections(id) ON DELETE CASCADE,
    FOREIGN KEY (group_id) REFERENCES variable_groups(id) ON DELETE CASCADE,
    UNIQUE(collection_id, group_id)
);
""",
    ),

    Migration(
        version=2,
        description="Add tags and folder support to requests",
        sql="""
-- Tags column on requests (comma-separated, kept simple to avoid extra tables)
ALTER TABLE requests ADD COLUMN tags TEXT DEFAULT '';

-- Folder/group name for logical grouping within a collection
ALTER TABLE requests ADD COLUMN folder TEXT DEFAULT '';
""",
    ),

    Migration(
        version=3,
        description="Add timeout and SSL settings to requests",
        sql="""
ALTER TABLE requests ADD COLUMN timeout    REAL    DEFAULT 30.0;
ALTER TABLE requests ADD COLUMN verify_ssl INTEGER DEFAULT 1;
""",
    ),

    Migration(
        version=4,
        description="Add follow_redirects to requests and size to history",
        sql="""
ALTER TABLE requests ADD COLUMN follow_redirects INTEGER DEFAULT 1;
ALTER TABLE history  ADD COLUMN response_size    INTEGER;
""",
    ),

    Migration(
        version=5,
        description="Add environment_id to history for context tracking",
        sql="""
ALTER TABLE history ADD COLUMN environment_id INTEGER REFERENCES environments(id) ON DELETE SET NULL;
""",
    ),

    Migration(
        version=6,
        description="Add performance indexes for history and request lookups",
        sql="""
CREATE INDEX IF NOT EXISTS idx_history_executed_at ON history(executed_at DESC);
CREATE INDEX IF NOT EXISTS idx_requests_collection ON requests(collection_id);
CREATE INDEX IF NOT EXISTS idx_history_url ON history(url);
""",
    ),

    Migration(
        version=7,
        description="Add oauth_clients table for named, reusable OAuth2 credentials",
        sql="""
CREATE TABLE IF NOT EXISTS oauth_clients (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT    NOT NULL UNIQUE,
    token_url     TEXT    NOT NULL,
    client_id     TEXT    NOT NULL,
    client_secret TEXT    NOT NULL DEFAULT '',
    scope         TEXT    DEFAULT '',
    grant_type    TEXT    NOT NULL DEFAULT 'client_credentials',
    extra_params  TEXT    DEFAULT '{}',
    description   TEXT    DEFAULT '',
    is_default    INTEGER DEFAULT 0,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_oauth_clients_default ON oauth_clients(is_default);
""",
    ),

    Migration(
        version=8,
        description="Add saved_credentials table for reusable auth configs of any type",
        sql="""
CREATE TABLE IF NOT EXISTS saved_credentials (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE,
    auth_type   TEXT    NOT NULL,
    config      TEXT    NOT NULL DEFAULT '{}',
    description TEXT    DEFAULT '',
    is_default  INTEGER DEFAULT 0,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_saved_creds_type    ON saved_credentials(auth_type);
CREATE INDEX IF NOT EXISTS idx_saved_creds_default ON saved_credentials(is_default);
INSERT OR IGNORE INTO saved_credentials
    (name, auth_type, config, description, is_default, created_at, updated_at)
SELECT
    name, 'oauth2',
    json_object(
        'token_url',     token_url,
        'client_id',     client_id,
        'client_secret', client_secret,
        'scope',         COALESCE(scope, ''),
        'grant_type',    COALESCE(grant_type, 'client_credentials'),
        'extra_params',  json(COALESCE(extra_params, '{}'))
    ),
    COALESCE(description, ''), is_default, created_at, updated_at
FROM oauth_clients;
""",
    ),

    Migration(
        version=9,
        description="Add captures column to requests for response variable extraction",
        sql="ALTER TABLE requests ADD COLUMN captures TEXT DEFAULT '[]';",
    ),

    Migration(
        version=10,
        description="Add pre_script, post_script, cert_path, cert_key_path to requests",
        sql="""
ALTER TABLE requests ADD COLUMN pre_script    TEXT DEFAULT '';
ALTER TABLE requests ADD COLUMN post_script   TEXT DEFAULT '';
ALTER TABLE requests ADD COLUMN cert_path     TEXT;
ALTER TABLE requests ADD COLUMN cert_key_path TEXT;
""",
    ),

    Migration(
        version=11,
        description="Add cookies table for persistent cookie jar",
        sql="""
CREATE TABLE IF NOT EXISTS cookies (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    value      TEXT NOT NULL DEFAULT '',
    domain     TEXT NOT NULL DEFAULT '',
    path       TEXT NOT NULL DEFAULT '/',
    secure     INTEGER DEFAULT 0,
    http_only  INTEGER DEFAULT 0,
    expires    TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(name, domain, path)
);
""",
    ),

    Migration(
        version=12,
        description="Add index on history status_code for faster response filtering",
        sql="""
CREATE INDEX IF NOT EXISTS idx_history_status_code ON history(status_code);
CREATE INDEX IF NOT EXISTS idx_history_method ON history(method);
""",
    ),

    Migration(
        version=13,
        description="Add collection_folders table for persistent empty folder records",
        sql="""
CREATE TABLE IF NOT EXISTS collection_folders (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    path          TEXT NOT NULL,
    description   TEXT DEFAULT '',
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(collection_id, path)
);
CREATE INDEX IF NOT EXISTS idx_collection_folders_collection
    ON collection_folders(collection_id);
""",
    ),

    Migration(
        version=14,
        description="Add sort_order to requests for manual reordering",
        sql="ALTER TABLE requests ADD COLUMN sort_order INTEGER DEFAULT 0;",
    ),

    Migration(
        version=15,
        description="Add hierarchical auth to collections and folders",
        sql="""
ALTER TABLE collections ADD COLUMN auth_type TEXT;
ALTER TABLE collections ADD COLUMN auth_data TEXT;
ALTER TABLE collection_folders ADD COLUMN auth_type TEXT;
ALTER TABLE collection_folders ADD COLUMN auth_data TEXT;
""",
    ),

    Migration(
        version=16,
        description="Add secret_keys column to environments for masked variable display",
        sql="ALTER TABLE environments ADD COLUMN secret_keys TEXT DEFAULT '[]';",
    ),

    Migration(
        version=17,
        description="Add assertions column to requests for post-response test rules",
        sql="ALTER TABLE requests ADD COLUMN assertions TEXT DEFAULT '[]';",
    ),

    Migration(
        version=18,
        description="Add path_params column to requests for structured path parameter values",
        sql="ALTER TABLE requests ADD COLUMN path_params TEXT DEFAULT '{}';",
    ),

    Migration(
        version=19,
        description="Add missing performance indexes for environments, collection variables, and variable groups",
        sql="""
CREATE INDEX IF NOT EXISTS idx_environments_active ON environments(is_active);
CREATE INDEX IF NOT EXISTS idx_collection_variables_collection ON collection_variables(collection_id);
CREATE INDEX IF NOT EXISTS idx_variable_group_items_group ON variable_group_items(group_id);
CREATE INDEX IF NOT EXISTS idx_collection_variable_groups_collection ON collection_variable_groups(collection_id);
CREATE INDEX IF NOT EXISTS idx_collection_variable_groups_group ON collection_variable_groups(group_id);
CREATE INDEX IF NOT EXISTS idx_collection_folders_collection ON collection_folders(collection_id);
""",
    ),

    Migration(
        version=20,
        description="Add Response Intelligence tables for endpoint stats and schema drift tracking",
        sql="""
CREATE TABLE IF NOT EXISTS endpoint_stats (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    url_pattern     TEXT    NOT NULL,
    method          TEXT    NOT NULL,
    call_count      INTEGER DEFAULT 0,
    total_elapsed   REAL    DEFAULT 0,
    min_elapsed     REAL,
    max_elapsed     REAL,
    elapsed_values  TEXT    DEFAULT '[]',
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_endpoint_stats_pattern
    ON endpoint_stats(url_pattern, method);

CREATE TABLE IF NOT EXISTS response_schemas (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    url_pattern     TEXT    NOT NULL,
    method          TEXT    NOT NULL,
    schema_hash     TEXT    NOT NULL,
    schema_json     TEXT    NOT NULL,
    captured_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_response_schemas_pattern
    ON response_schemas(url_pattern, method);
""",
    ),
]

# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────

class MigrationRunner:
    """Apply pending migrations to a database.

    The runner is intentionally *not* coupled to the :class:`Database` ORM
    layer — it works directly with ``sqlite3`` connections so it can run
    before any application code that depends on the schema being present.

    Args:
        db: :class:`~equinox.storage.database.Database` instance whose
            underlying SQLite file should be migrated.
    """

    VERSION_TABLE = "schema_version"

    def __init__(self, db: "Database") -> None:  # type: ignore[name-defined]
        self._db = db

    # ──────────────────────────────────────────────────────────────────────
    # Public interface
    # ──────────────────────────────────────────────────────────────────────

    @property
    def version(self) -> int:
        """Return the current schema version stored in the database (0 = empty)."""
        with self._db.get_connection() as conn:
            return self._read_version(conn)

    def run(self) -> int:
        """Apply all pending migrations in order.

        Returns:
            The new schema version after the run.

        Raises:
            StorageError: If any migration fails.  The database is left at the
                last successfully applied version.
        """
        # Open a dedicated connection with isolation_level=None (autocommit) so
        # we can use explicit BEGIN/COMMIT/ROLLBACK around DDL statements.
        # Python's default isolation_level='DEFERRED' silently auto-commits before
        # DDL (CREATE TABLE, ALTER TABLE), which breaks savepoint semantics.
        import sqlite3 as _sqlite3
        with self._db.lock:
            conn = _sqlite3.connect(
                str(self._db.db_path),
                check_same_thread=False,
                isolation_level=None,  # autocommit; we manage transactions manually
            )
            conn.row_factory = _sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            try:
                self._ensure_version_table(conn)
                current = self._read_version(conn)
                pending = [m for m in MIGRATIONS if m.version > current]

                if not pending:
                    logger.debug(
                        "Database already at version %d — no migrations needed", current
                    )
                    return current

                logger.info(
                    "Applying %d migration(s) to database (current version: %d)",
                    len(pending), current,
                )
                for migration in pending:
                    self._apply(conn, migration)

                new_version = pending[-1].version
                logger.info("Database migrated to version %d", new_version)
                return new_version
            finally:
                conn.close()

    def pending(self) -> List[Migration]:
        """Return migrations that have not yet been applied."""
        current = self.version
        return [m for m in MIGRATIONS if m.version > current]

    def history(self) -> List[dict]:
        """Return the log of applied migrations from the database."""
        with self._db.get_connection() as conn:
            try:
                rows = conn.execute(
                    f"SELECT version, description, applied_at "  # noqa: S608
                    f"FROM {self.VERSION_TABLE} ORDER BY version"
                ).fetchall()
                return [dict(row) for row in rows]
            except sqlite3.OperationalError:
                return []

    # ──────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────────────────────────────

    def _ensure_version_table(self, conn: sqlite3.Connection) -> None:
        conn.execute("BEGIN")
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {self.VERSION_TABLE} (
                version     INTEGER PRIMARY KEY,
                description TEXT    NOT NULL,
                applied_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("COMMIT")

    def _read_version(self, conn: sqlite3.Connection) -> int:
        try:
            row = conn.execute(
                f"SELECT MAX(version) FROM {self.VERSION_TABLE}"  # noqa: S608
            ).fetchone()
            return int(row[0]) if row and row[0] is not None else 0
        except sqlite3.OperationalError:
            return 0

    def _apply(self, conn: sqlite3.Connection, migration: Migration) -> None:
        """Execute one migration wrapped in an explicit transaction."""
        logger.info(
            "Applying migration v%d: %s", migration.version, migration.description
        )
        try:
            conn.execute("BEGIN")
            try:
                for stmt in self._split_sql(migration.sql):
                    self._execute_stmt(conn, stmt)
                conn.execute(
                    f"INSERT INTO {self.VERSION_TABLE} (version, description) VALUES (?, ?)",
                    (migration.version, migration.description),
                )
                conn.execute("COMMIT")
                logger.info("Migration v%d applied successfully", migration.version)
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
                raise
        except sqlite3.Error as exc:
            raise StorageError(
                f"Migration v{migration.version} failed: {exc}"
            ) from exc

    @staticmethod
    def _execute_stmt(conn: sqlite3.Connection, stmt: str) -> None:
        """Execute a single SQL statement with idempotency handling.

        ``ALTER TABLE ... ADD COLUMN`` is silently skipped when the column
        already exists — SQLite has no ``IF NOT EXISTS`` clause for this.
        All other statements are executed as-is.
        """
        import re as _re
        m = _re.match(
            r"ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN\s+(\w+)",
            stmt, _re.IGNORECASE,
        )
        if m:
            table, column = m.group(1), m.group(2)
            existing = {
                row[1]
                for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            if column in existing:
                logger.debug("Column %s.%s already exists — skipping", table, column)
                return
        conn.execute(stmt)

    @staticmethod
    def _split_sql(sql: str) -> list:
        """Split a SQL script into individual non-empty statements.

        Strips leading ``-- comment`` lines from each statement so that
        blocks like ``-- Collections\\nCREATE TABLE ...`` are not
        mistakenly discarded.
        """
        results = []
        for raw_stmt in sql.split(";"):
            # Remove pure-comment lines; keep the actual SQL lines.
            lines = [
                line for line in raw_stmt.splitlines()
                if line.strip() and not line.strip().startswith("--")
            ]
            cleaned = "\n".join(lines).strip()
            if cleaned:
                results.append(cleaned)
        return results

