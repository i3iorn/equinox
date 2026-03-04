"""Tests for CLI commands — variables and requests subcommands.

Uses Click CliRunner with ``get_db`` monkeypatched to return a temp DB.
"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from equinox.storage.database import Database
from equinox.storage.collections import CollectionManager
from equinox.storage.environments import EnvironmentManager
from equinox.storage import VariableGroupManager
from equinox.core.request import Request


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "cli_test.db"))


@pytest.fixture
def runner():
    return CliRunner()


def _patch_db(db):
    """Return a patch context that makes ``get_db`` return *db*."""
    return patch("equinox.cli.main.get_db", return_value=db)


# ── vargroup commands ─────────────────────────────────────────────────────────


class TestVargroupCLI:
    def test_list_empty(self, runner, db):
        from equinox.cli.variables import vargroup

        with _patch_db(db):
            result = runner.invoke(vargroup, ["list"])
        assert result.exit_code == 0
        assert "No variable groups" in result.output

    def test_create_and_list(self, runner, db):
        from equinox.cli.variables import vargroup

        with _patch_db(db):
            result = runner.invoke(vargroup, ["create", "Staging"])
        assert result.exit_code == 0
        assert "created" in result.output.lower()

        with _patch_db(db):
            result = runner.invoke(vargroup, ["list"])
        assert "Staging" in result.output

    def test_create_with_description(self, runner, db):
        from equinox.cli.variables import vargroup

        with _patch_db(db):
            result = runner.invoke(vargroup, ["create", "Prod", "-d", "Production vars"])
        assert result.exit_code == 0

    def test_delete_group(self, runner, db):
        from equinox.cli.variables import vargroup

        mgr = VariableGroupManager(db)
        gid = mgr.create_group("ToDelete", "")

        with _patch_db(db):
            result = runner.invoke(vargroup, ["delete", str(gid)])
        assert result.exit_code == 0
        assert "deleted" in result.output.lower()

    def test_add_var(self, runner, db):
        from equinox.cli.variables import vargroup

        mgr = VariableGroupManager(db)
        gid = mgr.create_group("G1", "")

        with _patch_db(db):
            result = runner.invoke(vargroup, ["add-var", str(gid), "API_KEY", "sk-123"])
        assert result.exit_code == 0
        assert "API_KEY" in result.output

    def test_add_var_with_description(self, runner, db):
        from equinox.cli.variables import vargroup

        mgr = VariableGroupManager(db)
        gid = mgr.create_group("G1", "")

        with _patch_db(db):
            result = runner.invoke(vargroup, ["add-var", str(gid), "TOKEN", "abc", "-d", "Auth token"])
        assert result.exit_code == 0

    def test_remove_var(self, runner, db):
        from equinox.cli.variables import vargroup

        mgr = VariableGroupManager(db)
        gid = mgr.create_group("G1", "")
        mgr.add_variable(gid, "FOO", "bar", "")

        with _patch_db(db):
            result = runner.invoke(vargroup, ["remove-var", str(gid), "FOO"])
        assert result.exit_code == 0
        assert "removed" in result.output.lower()

    def test_show_group(self, runner, db):
        from equinox.cli.variables import vargroup

        mgr = VariableGroupManager(db)
        gid = mgr.create_group("DevVars", "Dev environment")
        mgr.add_variable(gid, "DB_HOST", "localhost", "Database host")

        with _patch_db(db):
            result = runner.invoke(vargroup, ["show", str(gid)])
        assert result.exit_code == 0
        assert "DevVars" in result.output
        assert "DB_HOST" in result.output
        assert "localhost" in result.output

    def test_show_group_not_found(self, runner, db):
        from equinox.cli.variables import vargroup

        with _patch_db(db):
            result = runner.invoke(vargroup, ["show", "9999"])
        assert result.exit_code != 0

    def test_show_group_empty_variables(self, runner, db):
        from equinox.cli.variables import vargroup

        mgr = VariableGroupManager(db)
        gid = mgr.create_group("Empty", "")

        with _patch_db(db):
            result = runner.invoke(vargroup, ["show", str(gid)])
        assert result.exit_code == 0
        assert "No variables" in result.output

    def test_list_with_descriptions(self, runner, db):
        from equinox.cli.variables import vargroup

        mgr = VariableGroupManager(db)
        mgr.create_group("G1", "First group")
        mgr.create_group("G2", "")

        with _patch_db(db):
            result = runner.invoke(vargroup, ["list"])
        assert "G1" in result.output
        assert "First group" in result.output
        assert "G2" in result.output


# ── request auth commands ─────────────────────────────────────────────────────


class TestRequestAuthCLI:
    @pytest.fixture
    def req_id(self, db):
        mgr = CollectionManager(db)
        col_id = mgr.create_collection("TestCol")
        req = Request(method="GET", url="https://api.example.com", name="TestReq")
        return mgr.save_request(req, collection_id=col_id, name="TestReq")

    def test_auth_bearer(self, runner, db, req_id):
        from equinox.cli.requests import request

        with _patch_db(db):
            result = runner.invoke(request, ["auth", "bearer", str(req_id)], input="my-token\n")
        assert result.exit_code == 0
        assert "Bearer token configured" in result.output

    def test_auth_basic(self, runner, db, req_id):
        from equinox.cli.requests import request

        with _patch_db(db):
            result = runner.invoke(
                request, ["auth", "basic", str(req_id), "-u", "admin"],
                input="password123\n",
            )
        assert result.exit_code == 0
        assert "Basic auth configured" in result.output

    def test_auth_api_key(self, runner, db, req_id):
        from equinox.cli.requests import request

        with _patch_db(db):
            result = runner.invoke(
                request,
                ["auth", "api-key", str(req_id), "-n", "X-API-Key"],
                input="sk-12345\n",
            )
        assert result.exit_code == 0
        assert "API key configured" in result.output

    def test_auth_clear(self, runner, db, req_id):
        from equinox.cli.requests import request

        with _patch_db(db):
            result = runner.invoke(request, ["auth", "clear", str(req_id)])
        assert result.exit_code == 0
        assert "cleared" in result.output.lower()

    def test_auth_show(self, runner, db, req_id):
        from equinox.cli.requests import request

        with _patch_db(db):
            result = runner.invoke(request, ["auth", "show", str(req_id)])
        assert result.exit_code == 0
        assert "TestReq" in result.output

    def test_auth_show_nonexistent(self, runner, db):
        from equinox.cli.requests import request

        with _patch_db(db):
            result = runner.invoke(request, ["auth", "show", "9999"])
        assert result.exit_code != 0

    def test_auth_bearer_nonexistent(self, runner, db):
        from equinox.cli.requests import request

        with _patch_db(db):
            result = runner.invoke(request, ["auth", "bearer", "9999"], input="tok\n")
        assert result.exit_code != 0

    def test_auth_oauth2(self, runner, db, req_id):
        from equinox.cli.requests import request

        with _patch_db(db):
            result = runner.invoke(
                request,
                [
                    "auth", "oauth2", str(req_id),
                    "--token-url", "https://auth.example.com/token",
                    "--client-id", "my-client",
                ],
                input="my-secret\n",
            )
        assert result.exit_code == 0
        assert "OAuth2 configured" in result.output

    def test_auth_oauth2_with_scope(self, runner, db, req_id):
        from equinox.cli.requests import request

        with _patch_db(db):
            result = runner.invoke(
                request,
                [
                    "auth", "oauth2", str(req_id),
                    "--token-url", "https://auth.example.com/token",
                    "--client-id", "my-client",
                    "--scope", "read write",
                ],
                input="secret\n",
            )
        assert result.exit_code == 0
        assert "Scope: read write" in result.output

    def test_auth_api_key_query_location(self, runner, db, req_id):
        from equinox.cli.requests import request

        with _patch_db(db):
            result = runner.invoke(
                request,
                ["auth", "api-key", str(req_id), "-n", "api_key", "-l", "query"],
                input="sk-val\n",
            )
        assert result.exit_code == 0
        assert "query" in result.output


# ── env commands ──────────────────────────────────────────────────────────────


class TestEnvCLI:
    def test_list_empty(self, runner, db):
        from equinox.cli.environments import env

        with _patch_db(db):
            result = runner.invoke(env, ["list"])
        assert result.exit_code == 0
        assert "No environments" in result.output

    def test_create_and_list(self, runner, db):
        from equinox.cli.environments import env

        with _patch_db(db):
            result = runner.invoke(env, ["create", "Dev", "-v", "HOST=localhost"])
        assert result.exit_code == 0
        assert "created" in result.output.lower()

        with _patch_db(db):
            result = runner.invoke(env, ["list"])
        assert "Dev" in result.output

    def test_create_with_description(self, runner, db):
        from equinox.cli.environments import env

        with _patch_db(db):
            result = runner.invoke(env, ["create", "Staging", "-d", "Staging env"])
        assert result.exit_code == 0

    def test_activate(self, runner, db):
        from equinox.cli.environments import env

        mgr = EnvironmentManager(db)
        eid = mgr.create_environment("Prod", {"X": "1"})

        with _patch_db(db):
            result = runner.invoke(env, ["activate", str(eid)])
        assert result.exit_code == 0
        assert "activated" in result.output.lower()

    def test_show(self, runner, db):
        from equinox.cli.environments import env

        mgr = EnvironmentManager(db)
        eid = mgr.create_environment("Show", {"API_URL": "https://api.com"}, description="My API")

        with _patch_db(db):
            result = runner.invoke(env, ["show", str(eid)])
        assert result.exit_code == 0
        assert "Show" in result.output
        assert "API_URL" in result.output

    def test_show_not_found(self, runner, db):
        from equinox.cli.environments import env

        with _patch_db(db):
            result = runner.invoke(env, ["show", "9999"])
        assert result.exit_code != 0

    def test_show_empty_vars(self, runner, db):
        from equinox.cli.environments import env

        mgr = EnvironmentManager(db)
        eid = mgr.create_environment("Empty", {})

        with _patch_db(db):
            result = runner.invoke(env, ["show", str(eid)])
        assert result.exit_code == 0
        assert "No variables" in result.output

    def test_set_var(self, runner, db):
        from equinox.cli.environments import env

        mgr = EnvironmentManager(db)
        eid = mgr.create_environment("VarEnv", {"A": "1"})

        with _patch_db(db):
            result = runner.invoke(env, ["set-var", str(eid), "B", "2"])
        assert result.exit_code == 0
        assert "set" in result.output.lower()

    def test_set_var_not_found(self, runner, db):
        from equinox.cli.environments import env

        with _patch_db(db):
            result = runner.invoke(env, ["set-var", "9999", "K", "V"])
        assert result.exit_code != 0

    def test_remove_var(self, runner, db):
        from equinox.cli.environments import env

        mgr = EnvironmentManager(db)
        eid = mgr.create_environment("RemEnv", {"X": "1"})

        with _patch_db(db):
            result = runner.invoke(env, ["remove-var", str(eid), "X"])
        assert result.exit_code == 0
        assert "removed" in result.output.lower()

    def test_remove_var_not_found(self, runner, db):
        from equinox.cli.environments import env

        mgr = EnvironmentManager(db)
        eid = mgr.create_environment("RemEnv2", {"A": "1"})

        with _patch_db(db):
            result = runner.invoke(env, ["remove-var", str(eid), "MISSING"])
        assert result.exit_code != 0

    def test_delete_with_yes(self, runner, db):
        from equinox.cli.environments import env

        mgr = EnvironmentManager(db)
        eid = mgr.create_environment("DeleteMe", {})

        with _patch_db(db):
            result = runner.invoke(env, ["delete", str(eid), "--yes"])
        assert result.exit_code == 0
        assert "deleted" in result.output.lower()

    def test_delete_not_found(self, runner, db):
        from equinox.cli.environments import env

        with _patch_db(db):
            result = runner.invoke(env, ["delete", "9999", "--yes"])
        assert result.exit_code != 0

    def test_list_with_active_and_desc(self, runner, db):
        from equinox.cli.environments import env

        mgr = EnvironmentManager(db)
        eid = mgr.create_environment("Active", {"X": "1"}, description="Active env")
        mgr.set_active_environment(eid)

        with _patch_db(db):
            result = runner.invoke(env, ["list"])
        assert "(active)" in result.output
        assert "Active env" in result.output

    def test_import_dotenv(self, runner, db, tmp_path):
        from equinox.cli.environments import env

        mgr = EnvironmentManager(db)
        eid = mgr.create_environment("DotEnv", {"EXISTING": "keep"})

        dotenv_file = tmp_path / ".env"
        dotenv_file.write_text("NEW_KEY=new_value\nOTHER=123\n", encoding="utf-8")

        with _patch_db(db):
            result = runner.invoke(env, ["import-dotenv", str(eid), str(dotenv_file)])
        assert result.exit_code == 0
        assert "Imported 2" in result.output

        # Verify merge (default)
        updated = mgr.get_environment(eid)
        assert updated["variables"]["EXISTING"] == "keep"
        assert updated["variables"]["NEW_KEY"] == "new_value"

    def test_import_dotenv_replace(self, runner, db, tmp_path):
        from equinox.cli.environments import env

        mgr = EnvironmentManager(db)
        eid = mgr.create_environment("Replace", {"OLD": "gone"})

        dotenv_file = tmp_path / ".env"
        dotenv_file.write_text("FRESH=val\n", encoding="utf-8")

        with _patch_db(db):
            result = runner.invoke(env, ["import-dotenv", str(eid), str(dotenv_file), "--replace"])
        assert result.exit_code == 0

        updated = mgr.get_environment(eid)
        assert "OLD" not in updated["variables"]
        assert updated["variables"]["FRESH"] == "val"

    def test_import_dotenv_not_found(self, runner, db, tmp_path):
        from equinox.cli.environments import env

        dotenv_file = tmp_path / ".env"
        dotenv_file.write_text("K=V\n", encoding="utf-8")

        with _patch_db(db):
            result = runner.invoke(env, ["import-dotenv", "9999", str(dotenv_file)])
        assert result.exit_code != 0

    def test_import_dotenv_empty_file(self, runner, db, tmp_path):
        from equinox.cli.environments import env

        mgr = EnvironmentManager(db)
        eid = mgr.create_environment("EmptyDot", {})

        dotenv_file = tmp_path / ".env"
        dotenv_file.write_text("# just comments\n", encoding="utf-8")

        with _patch_db(db):
            result = runner.invoke(env, ["import-dotenv", str(eid), str(dotenv_file)])
        assert result.exit_code == 0
        assert "No variables" in result.output

