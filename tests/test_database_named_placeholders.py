from equinox.storage.database import Database


def test_named_placeholder_binding(tmp_path):
    db_path = tmp_path / "equinox_np.db"
    db = Database(str(db_path))

    db.execute("CREATE TABLE test_np (id INTEGER PRIMARY KEY AUTOINCREMENT, a INTEGER)")
    row_id = db.insert("INSERT INTO test_np (a) VALUES (:a)", {"a": 42})

    row = db.fetchone("SELECT a FROM test_np WHERE id = ?", (row_id,))
    assert row is not None
    assert row.get("a") == 42


