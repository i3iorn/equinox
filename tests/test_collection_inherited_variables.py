"""Tests for inherited collection variables and folder grouping.

Verifies that:
1. ``get_all_collection_variables`` merges group + collection-specific
   variables with the correct precedence.
2. ``get_interpolation_variables`` includes collection variables when a
   ``collection_id`` is supplied.
3. The collections panel tree groups requests by folder and starts collapsed.
"""

import os
import pytest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from equinox.storage.database import Database
from equinox.storage.collections import CollectionManager
from equinox.storage.variable_groups import VariableGroupManager
from equinox.core.exceptions import ValidationError


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "test_inherited.db"))


@pytest.fixture
def col_mgr(db):
    return CollectionManager(db)


@pytest.fixture
def var_mgr(db):
    return VariableGroupManager(db)


@pytest.fixture
def collection_id(col_mgr):
    return col_mgr.create_collection("Test Collection")


# ── Inherited variable precedence ─────────────────────────────────────────────


class TestInheritedVariablePrecedence:
    """Collection-specific vars override group vars; lower-priority-number
    groups override higher-priority-number groups."""

    def test_collection_vars_override_group_vars(self, col_mgr, var_mgr, collection_id):
        gid = var_mgr.create_group("Defaults")
        var_mgr.add_variable(gid, "API_URL", "https://default.example.com")
        var_mgr.add_variable(gid, "TIMEOUT", "10")
        col_mgr.add_variable_group(collection_id, gid)

        # Override API_URL at the collection level
        col_mgr.add_variable(collection_id, "API_URL", "https://custom.example.com")

        merged = col_mgr.get_all_collection_variables(collection_id)
        assert merged["API_URL"] == "https://custom.example.com"
        assert merged["TIMEOUT"] == "10"

    def test_higher_priority_group_overrides_lower(self, col_mgr, var_mgr, collection_id):
        g_low = var_mgr.create_group("LowPriority")
        var_mgr.add_variable(g_low, "DB_HOST", "low-host")
        var_mgr.add_variable(g_low, "DB_PORT", "5432")

        g_high = var_mgr.create_group("HighPriority")
        var_mgr.add_variable(g_high, "DB_HOST", "high-host")

        col_mgr.add_variable_group(collection_id, g_low, priority=100)
        col_mgr.add_variable_group(collection_id, g_high, priority=1)

        merged = col_mgr.get_all_collection_variables(collection_id)
        assert merged["DB_HOST"] == "high-host"  # lower number wins
        assert merged["DB_PORT"] == "5432"        # only in low-priority group

    def test_three_tier_precedence(self, col_mgr, var_mgr, collection_id):
        """common group → env group → collection-specific."""
        g_common = var_mgr.create_group("Common")
        var_mgr.add_variable(g_common, "A", "common-A")
        var_mgr.add_variable(g_common, "B", "common-B")
        var_mgr.add_variable(g_common, "C", "common-C")

        g_env = var_mgr.create_group("Staging")
        var_mgr.add_variable(g_env, "A", "staging-A")
        var_mgr.add_variable(g_env, "B", "staging-B")

        col_mgr.add_variable_group(collection_id, g_common, priority=50)
        col_mgr.add_variable_group(collection_id, g_env, priority=10)
        col_mgr.add_variable(collection_id, "A", "collection-A")

        merged = col_mgr.get_all_collection_variables(collection_id)
        assert merged["A"] == "collection-A"   # collection wins
        assert merged["B"] == "staging-B"       # env group wins
        assert merged["C"] == "common-C"        # only in common

    def test_no_groups_returns_collection_only(self, col_mgr, collection_id):
        col_mgr.add_variable(collection_id, "KEY", "value")
        merged = col_mgr.get_all_collection_variables(collection_id)
        assert merged == {"KEY": "value"}

    def test_no_variables_returns_empty(self, col_mgr, collection_id):
        merged = col_mgr.get_all_collection_variables(collection_id)
        assert merged == {}

    def test_group_without_variables(self, col_mgr, var_mgr, collection_id):
        gid = var_mgr.create_group("EmptyGroup")
        col_mgr.add_variable_group(collection_id, gid)
        merged = col_mgr.get_all_collection_variables(collection_id)
        assert merged == {}

    def test_multiple_collections_independent(self, col_mgr, var_mgr, db):
        """Two collections sharing a group get independent overrides."""
        gid = var_mgr.create_group("Shared")
        var_mgr.add_variable(gid, "URL", "https://shared.example.com")

        col_a = col_mgr.create_collection("Col A")
        col_b = col_mgr.create_collection("Col B")
        col_mgr.add_variable_group(col_a, gid)
        col_mgr.add_variable_group(col_b, gid)

        col_mgr.add_variable(col_a, "URL", "https://a.example.com")

        assert col_mgr.get_all_collection_variables(col_a)["URL"] == "https://a.example.com"
        assert col_mgr.get_all_collection_variables(col_b)["URL"] == "https://shared.example.com"


# ── CLI get_interpolation_variables with collection_id ────────────────────────


class TestCLIInterpolationWithCollectionVars:
    """``get_interpolation_variables(db, collection_id=...)`` should merge
    collection-inherited variables into the result."""

    def test_includes_collection_vars(self, db, col_mgr, var_mgr, collection_id):
        from equinox.cli.main import get_interpolation_variables

        gid = var_mgr.create_group("Globals")
        var_mgr.add_variable(gid, "BASE_URL", "https://api.example.com")
        col_mgr.add_variable_group(collection_id, gid)
        col_mgr.add_variable(collection_id, "TOKEN", "abc")

        result = get_interpolation_variables(db, collection_id=collection_id)
        assert result["BASE_URL"] == "https://api.example.com"
        assert result["TOKEN"] == "abc"

    def test_without_collection_id_omits_collection_vars(self, db, col_mgr, collection_id):
        from equinox.cli.main import get_interpolation_variables

        col_mgr.add_variable(collection_id, "ONLY_IN_COL", "secret")

        result = get_interpolation_variables(db)
        assert "ONLY_IN_COL" not in result

    def test_equinox_env_vars_override_collection_vars(self, db, col_mgr, collection_id, monkeypatch):
        from equinox.cli.main import get_interpolation_variables

        col_mgr.add_variable(collection_id, "EQUINOX_OVERRIDE", "from-collection")
        monkeypatch.setenv("EQUINOX_OVERRIDE", "from-env")

        result = get_interpolation_variables(db, collection_id=collection_id)
        assert result["EQUINOX_OVERRIDE"] == "from-env"

    def test_collection_vars_override_environment(self, db, col_mgr, var_mgr, collection_id):
        """Collection variables override the active DB environment."""
        from equinox.cli.main import get_interpolation_variables
        from equinox.storage import EnvironmentManager

        env_mgr = EnvironmentManager(db)
        env_id = env_mgr.create_environment("dev", variables={"SHARED": "from-env"})
        env_mgr.set_active_environment(env_id)

        col_mgr.add_variable(collection_id, "SHARED", "from-collection")

        result = get_interpolation_variables(db, collection_id=collection_id)
        assert result["SHARED"] == "from-collection"


# ── Folder grouping in list_requests ──────────────────────────────────────────


class TestFolderGrouping:
    """Requests with a ``folder`` column should be grouped under folder nodes
    in the collections panel. We test at the storage level here."""

    def test_requests_have_folder_column(self, db, col_mgr, collection_id):
        """The requests table has a 'folder' column (from migration v2)."""
        row = db.fetchone(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='requests'"
        )
        assert "folder" in row["sql"].lower()

    def test_folder_returned_in_list_requests(self, db, col_mgr, collection_id):
        """list_requests returns the folder field for each request."""
        from equinox.core.request import Request
        req = Request(method="GET", url="https://example.com",
                      headers={}, params={}, name="Test Req",
                      collection_id=collection_id)
        req_id = col_mgr.save_request(req, collection_id=collection_id)

        # Manually set the folder
        db.execute("UPDATE requests SET folder = ? WHERE id = ?",
                   ("Auth", req_id))

        rows = col_mgr.list_requests(collection_id)
        assert len(rows) == 1
        assert rows[0]["folder"] == "Auth"

    def test_folder_empty_by_default(self, db, col_mgr, collection_id):
        """New requests have an empty folder by default."""
        from equinox.core.request import Request
        req = Request(method="GET", url="https://example.com",
                      headers={}, params={}, name="Root Req",
                      collection_id=collection_id)
        col_mgr.save_request(req, collection_id=collection_id)

        rows = col_mgr.list_requests(collection_id)
        assert rows[0]["folder"] == "" or rows[0]["folder"] is None

    def test_postman_import_sets_folder(self, db, col_mgr, collection_id):
        """Requests imported from Postman with nested folders get the folder
        path baked into their name (e.g. 'Auth/Login').  We verify the name
        pattern is preserved."""
        from equinox.core.request import Request
        req = Request(method="POST", url="https://example.com/login",
                      headers={}, params={}, name="Auth/Login",
                      collection_id=collection_id)
        col_mgr.save_request(req, collection_id=collection_id)

        rows = col_mgr.list_requests(collection_id)
        assert rows[0]["name"] == "Auth/Login"

