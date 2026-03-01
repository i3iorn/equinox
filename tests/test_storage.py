"""Tests for storage modules (collections, environments, history)."""

import pytest
from pathlib import Path
from datetime import datetime

from equinox.storage.database import Database
from equinox.storage.collections import CollectionManager
from equinox.storage.environments import EnvironmentManager
from equinox.storage.history import HistoryManager
from equinox.core.request import Request, Response
from equinox.core.exceptions import StorageError


class TestDatabase:
    """Tests for Database class."""

    @pytest.fixture
    def db(self, tmp_path):
        """Create temporary database."""
        db_path = tmp_path / "test.db"
        return Database(str(db_path))

    def test_database_initialization(self, tmp_path):
        """Test database initialization."""
        db_path = tmp_path / "test.db"
        db = Database(str(db_path))
        assert db.db_path.exists()

    def test_execute_query(self, db):
        """Test executing a query."""
        db.execute(
            "CREATE TABLE IF NOT EXISTS test (id INTEGER PRIMARY KEY, name TEXT)",
            ()
        )
        db.execute("INSERT INTO test (name) VALUES (?)", ("test_value",))

        result = db.fetchone("SELECT name FROM test WHERE id = ?", (1,))
        assert result["name"] == "test_value"

    def test_fetchall(self, db):
        """Test fetching multiple rows."""
        db.execute(
            "CREATE TABLE IF NOT EXISTS test (id INTEGER PRIMARY KEY, name TEXT)",
            ()
        )
        db.execute("INSERT INTO test (name) VALUES (?)", ("value1",))
        db.execute("INSERT INTO test (name) VALUES (?)", ("value2",))

        results = db.fetchall("SELECT name FROM test", ())
        assert len(results) == 2

    def test_insert_returns_id(self, db):
        """Test insert returns last row ID."""
        db.execute(
            "CREATE TABLE IF NOT EXISTS test (id INTEGER PRIMARY KEY, name TEXT)",
            ()
        )
        row_id = db.insert("INSERT INTO test (name) VALUES (?)", ("test",))
        assert row_id == 1

    def test_invalid_query_raises_error(self, db):
        """Test that invalid SQL raises error."""
        with pytest.raises(StorageError):
            db.execute("INVALID SQL SYNTAX", ())

    def test_parameter_mismatch_raises_error(self, db):
        """Test that parameter mismatch raises error."""
        db.execute(
            "CREATE TABLE IF NOT EXISTS test (id INTEGER PRIMARY KEY, name TEXT)",
            ()
        )
        with pytest.raises(Exception):
            db.execute("SELECT * FROM test WHERE id = ?", ())  # Missing parameter


class TestCollectionManager:
    """Tests for CollectionManager."""

    @pytest.fixture
    def db(self, tmp_path):
        """Create temporary database."""
        db_path = tmp_path / "test.db"
        return Database(str(db_path))

    @pytest.fixture
    def manager(self, db):
        """Create CollectionManager."""
        return CollectionManager(db)

    def test_create_collection(self, manager):
        """Test creating a collection."""
        col_id = manager.create_collection("Test Collection", "Description")
        assert col_id > 0

        collections = manager.list_collections()
        assert len(collections) == 1
        assert collections[0]["name"] == "Test Collection"

    def test_delete_collection(self, manager):
        """Test deleting a collection."""
        col_id = manager.create_collection("Test", "Desc")
        manager.delete_collection(col_id)

        collections = manager.list_collections()
        assert len(collections) == 0

    def test_save_request(self, manager):
        """Test saving a request to collection."""
        col_id = manager.create_collection("Test", "Desc")

        request = Request(
            method="GET",
            url="https://api.example.com/users",
            headers={"Accept": "application/json"},
            name="List Users"
        )

        req_id = manager.save_request(request, col_id)
        assert req_id > 0

    def test_list_requests(self, manager):
        """Test listing requests in collection."""
        col_id = manager.create_collection("Test", "Desc")

        request = Request(
            method="GET",
            url="https://api.example.com/users",
            name="List Users"
        )
        manager.save_request(request, col_id)

        requests = manager.list_requests(col_id)
        assert len(requests) == 1
        assert requests[0]["name"] == "List Users"

    def test_get_request(self, manager):
        """Test getting a specific request."""
        col_id = manager.create_collection("Test", "Desc")

        request = Request(
            method="POST",
            url="https://api.example.com/users",
            body='{"name": "John"}',
            name="Create User"
        )
        req_id = manager.save_request(request, col_id)

        retrieved = manager.get_request(req_id)
        assert retrieved is not None
        assert retrieved.method == "POST"
        assert retrieved.name == "Create User"

    def test_delete_request(self, manager):
        """Test deleting a request."""
        col_id = manager.create_collection("Test", "Desc")
        request = Request(method="GET", url="https://api.example.com", name="Test")
        req_id = manager.save_request(request, col_id)

        manager.delete_request(req_id)

        requests = manager.list_requests(col_id)
        assert len(requests) == 0


class TestEnvironmentManager:
    """Tests for EnvironmentManager."""

    @pytest.fixture
    def db(self, tmp_path):
        """Create temporary database."""
        db_path = tmp_path / "test.db"
        return Database(str(db_path))

    @pytest.fixture
    def manager(self, db):
        """Create EnvironmentManager."""
        return EnvironmentManager(db)

    def test_create_environment(self, manager):
        """Test creating an environment."""
        variables = {"API_URL": "https://api.example.com", "API_KEY": "test-key"}
        env_id = manager.create_environment("Development", variables, "Dev environment")

        assert env_id > 0

        envs = manager.list_environments()
        assert len(envs) == 1
        assert envs[0]["name"] == "Development"

    def test_get_environment(self, manager):
        """Test getting an environment."""
        variables = {"API_URL": "https://api.example.com"}
        env_id = manager.create_environment("Test", variables)

        env = manager.get_environment(env_id)
        assert env is not None
        assert env["name"] == "Test"
        assert "API_URL" in env["variables"]

    def test_set_active_environment(self, manager):
        """Test setting active environment."""
        env_id = manager.create_environment("Test", {})
        manager.set_active_environment(env_id)

        active = manager.get_active_environment()
        assert active is not None
        assert active["id"] == env_id
        assert active["is_active"] == 1  # SQLite returns 1 for TRUE

    def test_only_one_active_environment(self, manager):
        """Test that only one environment can be active."""
        env1_id = manager.create_environment("Env1", {})
        env2_id = manager.create_environment("Env2", {})

        manager.set_active_environment(env1_id)
        manager.set_active_environment(env2_id)

        envs = manager.list_environments()
        active_count = sum(1 for env in envs if env["is_active"])
        assert active_count == 1

    def test_interpolate_variables(self, manager):
        """Test variable interpolation."""
        variables = {
            "BASE_URL": "https://api.example.com",
            "VERSION": "v1",
            "API_KEY": "secret-key"
        }
        env_id = manager.create_environment("Test", variables)
        manager.set_active_environment(env_id)

        text = "{{BASE_URL}}/{{VERSION}}/users?key={{API_KEY}}"
        result = manager.interpolate_variables(text)

        assert result == "https://api.example.com/v1/users?key=secret-key"

    def test_delete_environment(self, manager):
        """Test deleting an environment."""
        env_id = manager.create_environment("Test", {})
        manager.delete_environment(env_id)

        envs = manager.list_environments()
        assert len(envs) == 0


class TestHistoryManager:
    """Tests for HistoryManager."""

    @pytest.fixture
    def db(self, tmp_path):
        """Create temporary database."""
        db_path = tmp_path / "test.db"
        return Database(str(db_path))

    @pytest.fixture
    def manager(self, db):
        """Create HistoryManager."""
        return HistoryManager(db)

    def test_save_history_success(self, manager):
        """Test saving successful request history."""
        request = Request(
            method="GET",
            url="https://api.example.com/users"
        )

        response = Response(
            status_code=200,
            reason="OK",
            headers={"content-type": "application/json"},
            body=b'{"users": []}',
            elapsed=0.5,
            request=request,
            timestamp=datetime.now()
        )

        history_id = manager.save_history(request, response)
        assert history_id > 0

    def test_save_history_failure(self, manager):
        """Test saving failed request history."""
        request = Request(
            method="POST",
            url="https://api.example.com/users"
        )

        history_id = manager.save_history(request, None, error="Connection timeout")
        assert history_id > 0

    def test_list_history(self, manager):
        """Test listing history entries."""
        request = Request(method="GET", url="https://api.example.com")
        response = Response(
            status_code=200,
            reason="OK",
            headers={},
            body=b"",
            elapsed=0.1,
            request=request,
            timestamp=datetime.now()
        )

        manager.save_history(request, response)
        manager.save_history(request, response)

        history = manager.list_history(limit=10)
        assert len(history) == 2

    def test_list_history_with_limit(self, manager):
        """Test history limit."""
        request = Request(method="GET", url="https://api.example.com")
        response = Response(
            status_code=200,
            reason="OK",
            headers={},
            body=b"",
            elapsed=0.1,
            request=request,
            timestamp=datetime.now()
        )

        for _ in range(5):
            manager.save_history(request, response)

        history = manager.list_history(limit=3)
        assert len(history) == 3

    def test_get_history_entry(self, manager):
        """Test getting a specific history entry."""
        request = Request(method="GET", url="https://api.example.com")
        response = Response(
            status_code=200,
            reason="OK",
            headers={},
            body=b'{"test": "data"}',
            elapsed=0.1,
            request=request,
            timestamp=datetime.now()
        )

        history_id = manager.save_history(request, response)
        entry = manager.get_history(history_id)

        assert entry is not None
        assert entry["method"] == "GET"
        assert entry["status_code"] == 200

    def test_clear_history(self, manager):
        """Test clearing all history."""
        request = Request(method="GET", url="https://api.example.com")
        response = Response(
            status_code=200,
            reason="OK",
            headers={},
            body=b"",
            elapsed=0.1,
            request=request,
            timestamp=datetime.now()
        )

        manager.save_history(request, response)
        manager.clear_history()

        history = manager.list_history()
        assert len(history) == 0
