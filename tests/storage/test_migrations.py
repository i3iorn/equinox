"""Tests for the database migration system."""

import sqlite3
import pytest
from pathlib import Path

from equinox.storage import Database, MigrationRunner, MIGRATIONS
from equinox.storage.migrations import Migration
from equinox.core.exceptions import StorageError


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "test.db"))


class TestMigrationRunner:

    def test_fresh_db_runs_all_migrations(self, db):
        runner = MigrationRunner(db)
        # DB was already migrated during __init__; version == latest
        assert runner.version == MIGRATIONS[-1].version

    def test_version_zero_before_any_migration(self, tmp_path):
        # Create a bare SQLite file with no schema at all
        db_path = tmp_path / "bare.db"
        conn = sqlite3.connect(str(db_path))
        conn.close()

        db = Database.__new__(Database)
        db.db_path = db_path.resolve()
        db.db_path.parent.mkdir(parents=True, exist_ok=True)
        import threading
        db._lock = threading.Lock()
        db._conn = sqlite3.connect(str(db.db_path))
        db._conn.row_factory = sqlite3.Row
        # Don't call _run_migrations yet
        runner = MigrationRunner(db)
        assert runner.version == 0
        db.close()

    def test_run_is_idempotent(self, db):
        runner = MigrationRunner(db)
        v1 = runner.run()
        v2 = runner.run()
        assert v1 == v2 == MIGRATIONS[-1].version

    def test_pending_empty_after_run(self, db):
        runner = MigrationRunner(db)
        assert runner.pending() == []

    def test_history_records_every_migration(self, db):
        runner = MigrationRunner(db)
        history = runner.history()
        assert len(history) == len(MIGRATIONS)
        versions = [h["version"] for h in history]
        assert versions == sorted(versions)  # ascending order
        assert set(versions) == {m.version for m in MIGRATIONS}

    def test_history_has_descriptions(self, db):
        runner = MigrationRunner(db)
        for entry in runner.history():
            assert entry["description"]
            assert entry["applied_at"]

    def test_only_pending_migrations_run(self, tmp_path):
        """Simulate a DB that already has v1 applied."""
        db = Database(str(tmp_path / "partial.db"))
        runner = MigrationRunner(db)

        # Manually set the version back to 1 to simulate a partially-migrated DB.
        with db.get_connection() as conn:
            conn.execute(
                "DELETE FROM schema_version WHERE version > 1"
            )
            conn.commit()

        assert runner.version == 1
        pending = runner.pending()
        assert all(m.version > 1 for m in pending)

        # Run should only apply the pending ones
        runner.run()
        assert runner.version == MIGRATIONS[-1].version
        db.close()

    def test_migrations_ordered_by_version(self):
        versions = [m.version for m in MIGRATIONS]
        assert versions == sorted(versions), "Migrations must be in ascending version order"

    def test_migration_versions_are_unique(self):
        versions = [m.version for m in MIGRATIONS]
        assert len(versions) == len(set(versions)), "Migration versions must be unique"

    def test_all_core_tables_created(self, db):
        with db.get_connection() as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        expected = {
            "collections", "requests", "history", "environments",
            "collection_variables", "variable_groups",
            "variable_group_items", "collection_variable_groups",
            "global_variables",
            "schema_version",
        }
        assert expected.issubset(tables)

    def test_v2_columns_added(self, db):
        """Migration v2 adds tags and folder columns to requests."""
        with db.get_connection() as conn:
            cols = {
                row[1]
                for row in conn.execute("PRAGMA table_info(requests)").fetchall()
            }
        assert "tags" in cols
        assert "folder" in cols

    def test_v3_columns_added(self, db):
        """Migration v3 adds timeout and verify_ssl to requests."""
        with db.get_connection() as conn:
            cols = {
                row[1]
                for row in conn.execute("PRAGMA table_info(requests)").fetchall()
            }
        assert "timeout" in cols
        assert "verify_ssl" in cols

    def test_v4_columns_added(self, db):
        """Migration v4 adds follow_redirects and response_size."""
        with db.get_connection() as conn:
            req_cols = {
                row[1]
                for row in conn.execute("PRAGMA table_info(requests)").fetchall()
            }
            hist_cols = {
                row[1]
                for row in conn.execute("PRAGMA table_info(history)").fetchall()
            }
        assert "follow_redirects" in req_cols
        assert "response_size" in hist_cols

    def test_v5_column_added(self, db):
        """Migration v5 adds environment_id to history."""
        with db.get_connection() as conn:
            cols = {
                row[1]
                for row in conn.execute("PRAGMA table_info(history)").fetchall()
            }
        assert "environment_id" in cols

    def test_bad_migration_raises_storage_error(self, db):
        """A broken migration should raise StorageError."""
        bad = Migration(version=9999, description="Bad migration", sql="INVALID SQL @@##")
        runner = MigrationRunner(db)
        # _apply requires an autocommit connection (isolation_level=None)
        conn = sqlite3.connect(str(db.db_path), isolation_level=None)
        try:
            with pytest.raises((StorageError, sqlite3.OperationalError)):
                runner._apply(conn, bad)
        finally:
            conn.close()


class TestMigrationIntegrationWithDatabase:

    def test_new_database_fully_migrated(self, tmp_path):
        with Database(str(tmp_path / "new.db")) as db:
            runner = MigrationRunner(db)
            assert runner.version == MIGRATIONS[-1].version
            assert runner.pending() == []

    def test_database_usable_after_migration(self, db):
        from equinox.storage import CollectionManager
        mgr = CollectionManager(db)
        col_id = mgr.create_collection("Test Collection")
        assert col_id > 0
        col = mgr.get_collection(col_id)
        assert col["name"] == "Test Collection"

    def test_v19_indexes_created(self, db):
        """Migration v19 adds missing performance indexes."""
        with db.get_connection() as conn:
            indexes = {
                row[1]
                for row in conn.execute(
                    "SELECT * FROM sqlite_master WHERE type='index'"
                ).fetchall()
                if row[1]
            }
        expected = {
            "idx_environments_active",
            "idx_collection_variables_collection",
            "idx_variable_group_items_group",
            "idx_collection_variable_groups_collection",
            "idx_collection_variable_groups_group",
            "idx_collection_folders_collection",
        }
        assert expected.issubset(indexes), f"Missing indexes: {expected - indexes}"


class TestDatabaseTransaction:
    """Tests for Database.transaction() context manager."""

    def test_transaction_commits_on_success(self, db):
        with db.transaction() as tx:
            tx.execute(
                "INSERT INTO collections (name, description) VALUES (?, ?)",
                ("TxCol", ""),
            )
        row = db.fetchone("SELECT name FROM collections WHERE name = ?", ("TxCol",))
        assert row is not None
        assert row["name"] == "TxCol"

    def test_transaction_rolls_back_on_error(self, db):
        with pytest.raises(RuntimeError):
            with db.transaction() as tx:
                tx.execute(
                    "INSERT INTO collections (name, description) VALUES (?, ?)",
                    ("RollbackCol", ""),
                )
                raise RuntimeError("Force rollback")
        row = db.fetchone("SELECT name FROM collections WHERE name = ?", ("RollbackCol",))
        assert row is None

    def test_transaction_multiple_inserts_atomic(self, db):
        with db.transaction() as tx:
            tx.execute(
                "INSERT INTO collections (name, description) VALUES (?, ?)",
                ("AtomicA", ""),
            )
            tx.execute(
                "INSERT INTO collections (name, description) VALUES (?, ?)",
                ("AtomicB", ""),
            )
        rows = db.fetchall("SELECT name FROM collections WHERE name LIKE ?", ("Atomic%",))
        assert len(rows) == 2

    def test_transaction_fetchone(self, db):
        db.execute(
            "INSERT INTO collections (name, description) VALUES (?, ?)",
            ("FetchOne", ""),
        )
        with db.transaction() as tx:
            row = tx.fetchone(
                "SELECT name FROM collections WHERE name = ?", ("FetchOne",)
            )
        assert row is not None
        assert row["name"] == "FetchOne"

    def test_transaction_fetchall(self, db):
        db.execute(
            "INSERT INTO collections (name, description) VALUES (?, ?)",
            ("FetchAll1", ""),
        )
        db.execute(
            "INSERT INTO collections (name, description) VALUES (?, ?)",
            ("FetchAll2", ""),
        )
        with db.transaction() as tx:
            rows = tx.fetchall(
                "SELECT name FROM collections WHERE name LIKE ?", ("FetchAll%",)
            )
        assert len(rows) == 2

    def test_transaction_insert_returns_rowid(self, db):
        with db.transaction() as tx:
            row_id = tx.insert(
                "INSERT INTO collections (name, description) VALUES (?, ?)",
                ("RowIdCol", ""),
            )
        assert isinstance(row_id, int)
        assert row_id > 0

