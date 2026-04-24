"""Focused tests for ``equinox.storage.variable_groups`` with full branch coverage."""

from __future__ import annotations

import pytest

from equinox.core.exceptions import DuplicateError, SecurityError, StorageError, ValidationError
from equinox.storage import Database
from equinox.storage.variable_groups import VariableGroupManager


class _Cursor:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


class _CreateGroupDuplicateDb:
    def fetchone(self, query, params=()):
        return {"count": 0}

    def insert(self, query, params=()):
        raise DuplicateError("dupe")


class _CreateGroupErrorDb:
    def fetchone(self, query, params=()):
        return {"count": 0}

    def insert(self, query, params=()):
        raise RuntimeError("boom")


class _CreateGroupNoCountDb:
    def __init__(self) -> None:
        self.insert_calls = 0

    def fetchone(self, query, params=()):
        return None

    def insert(self, query, params=()):
        self.insert_calls += 1
        return 123


class _UpdateGroupDuplicateDb:
    def fetchone(self, query, params=()):
        return {"id": params[0], "name": "existing", "description": "d"}

    def execute(self, query, params=()):
        raise DuplicateError("dupe")


class _UpdateGroupErrorDb:
    def fetchone(self, query, params=()):
        return {"id": params[0], "name": "existing", "description": "d"}

    def execute(self, query, params=()):
        raise RuntimeError("boom")


class _DeleteGroupErrorDb:
    def fetchone(self, query, params=()):
        if "FROM variable_groups" in query:
            return {"id": params[0], "name": "to-delete", "description": ""}
        return {"cnt": 2}

    def execute(self, query, params=()):
        raise RuntimeError("boom")


class _AddVariableErrorDb:
    def fetchone(self, query, params=()):
        if "FROM variable_groups" in query:
            return {"id": params[0], "name": "g", "description": ""}
        return {"count": 0}

    def insert(self, query, params=()):
        raise RuntimeError("boom")


class _RemoveVariableErrorDb:
    def execute(self, query, params=()):
        raise RuntimeError("boom")


@pytest.fixture
def db(tmp_path):
    """Create a real temporary DB for integration-style manager tests."""
    return Database(str(tmp_path / "variable_groups.db"))


@pytest.fixture
def manager(db):
    return VariableGroupManager(db)


def test_require_group_returns_row(manager):
    gid = manager.create_group("Core", "Main")
    row = manager._require_group(gid)
    assert row["id"] == gid


def test_require_group_missing_raises_storage_error(manager):
    with pytest.raises(StorageError, match="does not exist"):
        manager._require_group(999999)


def test_create_group_success_and_get_list(manager):
    g1 = manager.create_group("Beta", "B")
    g2 = manager.create_group("Alpha", "A")

    assert manager.get_group(g1)["name"] == "Beta"
    assert [g["name"] for g in manager.list_groups()] == ["Alpha", "Beta"]
    assert g2 > 0


def test_create_group_validation_invalid_name(manager):
    with pytest.raises(ValidationError):
        manager.create_group("   ", "desc")


def test_create_group_max_groups_limit(manager):
    manager.MAX_GROUPS = 0
    with pytest.raises(SecurityError, match="Maximum number of variable groups reached"):
        manager.create_group("Any", "desc")


def test_create_group_duplicate_error_is_rewrapped():
    mgr = VariableGroupManager(_CreateGroupDuplicateDb())
    with pytest.raises(DuplicateError, match="already exists"):
        mgr.create_group("Duped", "desc")


def test_create_group_generic_error_is_wrapped_storage_error():
    mgr = VariableGroupManager(_CreateGroupErrorDb())
    with pytest.raises(StorageError, match="Failed to create variable group"):
        mgr.create_group("Broken", "desc")


def test_create_group_when_count_query_returns_none():
    fake_db = _CreateGroupNoCountDb()
    mgr = VariableGroupManager(fake_db)
    gid = mgr.create_group("NoCount", "desc")
    assert gid == 123
    assert fake_db.insert_calls == 1


def test_get_group_requires_positive_int(manager):
    with pytest.raises(ValidationError):
        manager.get_group(0)


def test_update_group_updates_name_and_description(manager):
    gid = manager.create_group("Old", "old")
    manager.update_group(gid, name="New", description="new")
    row = manager.get_group(gid)
    assert row["name"] == "New"
    assert row["description"] == "new"


def test_update_group_with_no_fields_logs_and_returns(manager, caplog):
    gid = manager.create_group("NoChange", "x")
    with caplog.at_level("WARNING"):
        manager.update_group(gid)
    assert "No updates provided" in caplog.text


def test_update_group_duplicate_error_is_rewrapped():
    mgr = VariableGroupManager(_UpdateGroupDuplicateDb())
    with pytest.raises(DuplicateError, match="already exists"):
        mgr.update_group(1, name="new")


def test_update_group_generic_error_is_wrapped_storage_error():
    mgr = VariableGroupManager(_UpdateGroupErrorDb())
    with pytest.raises(StorageError, match="Failed to update variable group"):
        mgr.update_group(1, name="new")


def test_delete_group_success_with_count_row_none(manager, monkeypatch):
    gid = manager.create_group("DeleteMe", "desc")

    original_fetchone = manager.db.fetchone

    def _fetchone(query, params=()):
        if "SELECT COUNT(*) AS cnt FROM variable_group_items" in query:
            return None
        return original_fetchone(query, params)

    monkeypatch.setattr(manager.db, "fetchone", _fetchone)
    manager.delete_group(gid)
    assert manager.get_group(gid) is None


def test_delete_group_generic_error_is_wrapped_storage_error():
    mgr = VariableGroupManager(_DeleteGroupErrorDb())
    with pytest.raises(StorageError, match="Failed to delete variable group"):
        mgr.delete_group(1)


def test_add_variable_success_list_and_dict(manager):
    gid = manager.create_group("Vars", "desc")
    vid = manager.add_variable(gid, "API_URL", "https://example.com", "base")
    assert vid > 0

    vars_rows = manager.list_group_variables(gid)
    assert len(vars_rows) == 1
    assert vars_rows[0]["key"] == "API_URL"
    assert manager.get_group_variables_dict(gid) == {"API_URL": "https://example.com"}


def test_add_variable_updates_existing_when_group_at_limit(manager):
    gid = manager.create_group("Limit", "desc")
    manager.MAX_VARIABLES_PER_GROUP = 1

    manager.add_variable(gid, "TOKEN", "old")
    manager.add_variable(gid, "TOKEN", "new")

    rows = manager.list_group_variables(gid)
    assert len(rows) == 1
    assert rows[0]["value"] == "new"


def test_add_variable_rejects_new_key_when_group_at_limit(manager):
    gid = manager.create_group("LimitNew", "desc")
    manager.MAX_VARIABLES_PER_GROUP = 1

    manager.add_variable(gid, "FIRST", "1")
    with pytest.raises(SecurityError, match="Maximum number of variables per group reached"):
        manager.add_variable(gid, "SECOND", "2")


def test_add_variable_validation_paths(manager):
    gid = manager.create_group("Validation", "desc")

    with pytest.raises(ValidationError):
        manager.add_variable(gid, "", "value")
    with pytest.raises(ValidationError):
        manager.add_variable(gid, "VALID", "v" * (manager.MAX_VARIABLE_VALUE_LENGTH + 1))


def test_add_variable_generic_error_is_wrapped_storage_error():
    mgr = VariableGroupManager(_AddVariableErrorDb())
    with pytest.raises(StorageError, match="Failed to add variable"):
        mgr.add_variable(1, "KEY", "value")


def test_remove_variable_success(manager):
    gid = manager.create_group("Remove", "desc")
    manager.add_variable(gid, "K", "V")

    manager.remove_variable(gid, "K")
    assert manager.list_group_variables(gid) == []


def test_remove_variable_not_found_raises_storage_error(manager):
    gid = manager.create_group("RemoveMissing", "desc")
    with pytest.raises(StorageError, match="not found"):
        manager.remove_variable(gid, "MISSING")


def test_remove_variable_generic_error_is_wrapped_storage_error():
    mgr = VariableGroupManager(_RemoveVariableErrorDb())
    with pytest.raises(StorageError, match="Failed to remove variable"):
        mgr.remove_variable(1, "KEY")


def test_remove_variable_validation_invalid_group_or_key(manager):
    with pytest.raises(ValidationError):
        manager.remove_variable(0, "KEY")

    gid = manager.create_group("InvalidKey", "desc")
    with pytest.raises(ValidationError):
        manager.remove_variable(gid, "")


def test_list_group_variables_requires_positive_int(manager):
    with pytest.raises(ValidationError):
        manager.list_group_variables(-1)

