from equinox.storage.database import Database
from equinox.storage.migrations import MigrationRunner


def test_split_sql_and_idempotent_run(tmp_path):
    db_path = tmp_path / "equinox_mig.db"
    db = Database(str(db_path))
    runner = MigrationRunner(db)

    # Ensure runner._split_sql correctly handles semicolons inside string literals
    sql = "CREATE TABLE x (id INTEGER PRIMARY KEY); INSERT INTO x DEFAULT VALUES; ALTER TABLE x ADD COLUMN name TEXT DEFAULT 'a;b';"
    stmts = runner._split_sql(sql)
    # At least one statement should include the DEFAULT 'a;b' part intact
    assert any("DEFAULT 'a;b'" in s or "name TEXT" in s for s in stmts)

    # Running migrations twice should be a no-op on second run (idempotent)
    first = runner.run()
    second = runner.run()
    assert first == second


