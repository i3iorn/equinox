"""CLI smoke tests — exercises every top-level command with CliRunner."""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from click.testing import CliRunner

from equinox.cli.main import cli


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def temp_db(tmp_path):
    """Set EQUINOX_DB_PATH so all CLI commands use a throwaway DB."""
    db_path = str(tmp_path / "test.db")
    with patch.dict(os.environ, {"EQUINOX_DB_PATH": db_path}):
        yield db_path


# ── Help smoke tests ─────────────────────────────────────────────────────────

class TestHelpSmoke:
    """Every command and group should render --help without crashing."""

    @pytest.mark.parametrize("args", [
        ["--help"],
        ["get", "--help"],
        ["post", "--help"],
        ["put", "--help"],
        ["patch", "--help"],
        ["delete", "--help"],
        ["collection", "--help"],
        ["collection", "list", "--help"],
        ["history", "--help"],
        ["history", "list", "--help"],
        ["history", "export", "--help"],
        ["env", "--help"],
        ["env", "list", "--help"],
        ["env", "create", "--help"],
        ["env", "activate", "--help"],
        ["env", "delete", "--help"],
        ["env", "show", "--help"],
        ["env", "set-var", "--help"],
        ["env", "remove-var", "--help"],
        ["request", "--help"],
        ["request", "run", "--help"],
        ["import", "--help"],
        ["import", "postman", "--help"],
        ["import", "openapi", "--help"],
        ["import", "har", "--help"],
        ["vargroup", "--help"],
    ])
    def test_help_renders(self, runner, args):
        result = runner.invoke(cli, args)
        assert result.exit_code == 0, result.output
        assert "Usage:" in result.output or "Options:" in result.output


# ── Collection commands ──────────────────────────────────────────────────────

class TestCollectionCLI:
    def test_list_empty(self, runner, temp_db):
        result = runner.invoke(cli, ["collection", "list"])
        assert result.exit_code == 0
        assert "No collections" in result.output

    def test_create_and_list(self, runner, temp_db):
        result = runner.invoke(cli, ["collection", "create", "My API"])
        assert result.exit_code == 0
        assert "created" in result.output.lower() or "ID" in result.output

        result = runner.invoke(cli, ["collection", "list"])
        assert result.exit_code == 0
        assert "My API" in result.output


# ── History commands ─────────────────────────────────────────────────────────

class TestHistoryCLI:
    def test_list_empty(self, runner, temp_db):
        result = runner.invoke(cli, ["history", "list"])
        assert result.exit_code == 0
        assert "No history" in result.output

    def test_export_empty(self, runner, temp_db, tmp_path):
        out = str(tmp_path / "export.json")
        result = runner.invoke(cli, ["history", "export", "-o", out])
        assert result.exit_code == 0
        assert "No history" in result.output


# ── Environment commands (#14) ───────────────────────────────────────────────

class TestEnvCLI:
    def test_list_empty(self, runner, temp_db):
        result = runner.invoke(cli, ["env", "list"])
        assert result.exit_code == 0
        assert "No environments" in result.output

    def test_create_activate_show(self, runner, temp_db):
        result = runner.invoke(cli, ["env", "create", "staging",
                                     "-v", "BASE_URL=https://staging.example.com",
                                     "-d", "Staging environment"])
        assert result.exit_code == 0
        assert "created" in result.output.lower()

        result = runner.invoke(cli, ["env", "list"])
        assert "staging" in result.output

        result = runner.invoke(cli, ["env", "activate", "1"])
        assert result.exit_code == 0

        result = runner.invoke(cli, ["env", "show", "1"])
        assert result.exit_code == 0
        assert "staging" in result.output
        assert "BASE_URL" in result.output

    def test_set_var_and_remove_var(self, runner, temp_db):
        runner.invoke(cli, ["env", "create", "test-env"])

        result = runner.invoke(cli, ["env", "set-var", "1", "API_KEY", "secret123"])
        assert result.exit_code == 0
        assert "set" in result.output.lower()

        result = runner.invoke(cli, ["env", "show", "1"])
        assert "API_KEY" in result.output

        result = runner.invoke(cli, ["env", "remove-var", "1", "API_KEY"])
        assert result.exit_code == 0
        assert "removed" in result.output.lower()

    def test_delete_env(self, runner, temp_db):
        runner.invoke(cli, ["env", "create", "to-delete"])
        result = runner.invoke(cli, ["env", "delete", "1", "--yes"])
        assert result.exit_code == 0
        assert "deleted" in result.output.lower()

    def test_show_nonexistent(self, runner, temp_db):
        result = runner.invoke(cli, ["env", "show", "999"])
        assert result.exit_code != 0

    def test_remove_var_nonexistent_key(self, runner, temp_db):
        runner.invoke(cli, ["env", "create", "test"])
        result = runner.invoke(cli, ["env", "remove-var", "1", "NOPE"])
        assert result.exit_code != 0


# ── Variable group commands ──────────────────────────────────────────────────

class TestVarGroupCLI:
    def test_list_empty(self, runner, temp_db):
        result = runner.invoke(cli, ["vargroup", "list"])
        assert result.exit_code == 0


# ── Quiet flag ───────────────────────────────────────────────────────────────

class TestQuietFlag:
    """The --quiet flag should be accepted by all HTTP commands."""

    @pytest.mark.parametrize("cmd", ["get", "post", "put", "patch", "delete"])
    def test_quiet_accepted(self, runner, cmd):
        result = runner.invoke(cli, [cmd, "--help"])
        assert "--quiet" in result.output or "-q" in result.output


# ── Helpers for HTTP command tests ────────────────────────────────────────────

def _make_mock_response(status=200, body='{"ok": true}', method="GET",
                        url="https://example.com"):
    """Return a real Response object suitable for use as a mock return value."""
    from equinox.core.request import Request, Response
    req = Request(method=method, url=url)
    return Response(
        status_code=status,
        reason="OK" if status < 400 else "Error",
        headers={"content-type": "application/json"},
        body=body.encode(),
        elapsed=0.05,
        request=req,
    )


# ── HTTP command tests ────────────────────────────────────────────────────────

class TestHttpCommands:
    """Tests for get / post / put / patch / delete CLI commands."""

    @patch("equinox.core.client.HTTPClient.send")
    def test_get(self, mock_send, runner, temp_db):
        mock_send.return_value = _make_mock_response()
        result = runner.invoke(cli, ["get", "https://example.com"],
                               env={"EQUINOX_DB_PATH": temp_db})
        assert result.exit_code == 0, result.output
        assert "200" in result.output

    @patch("equinox.core.client.HTTPClient.send")
    def test_post_json(self, mock_send, runner, temp_db):
        mock_send.return_value = _make_mock_response(status=201, method="POST")
        result = runner.invoke(
            cli,
            ["post", "https://example.com", "--json", '{"name":"Alice"}'],
            env={"EQUINOX_DB_PATH": temp_db},
        )
        assert result.exit_code == 0, result.output
        assert "201" in result.output

    @patch("equinox.core.client.HTTPClient.send")
    def test_put(self, mock_send, runner, temp_db):
        mock_send.return_value = _make_mock_response(status=200, method="PUT")
        result = runner.invoke(
            cli,
            ["put", "https://example.com/1", "--json", '{"x":1}'],
            env={"EQUINOX_DB_PATH": temp_db},
        )
        assert result.exit_code == 0, result.output

    @patch("equinox.core.client.HTTPClient.send")
    def test_patch(self, mock_send, runner, temp_db):
        mock_send.return_value = _make_mock_response(status=200, method="PATCH")
        result = runner.invoke(
            cli,
            ["patch", "https://example.com/1", "--json", '{"y":2}'],
            env={"EQUINOX_DB_PATH": temp_db},
        )
        assert result.exit_code == 0, result.output

    @patch("equinox.core.client.HTTPClient.send")
    def test_delete(self, mock_send, runner, temp_db):
        mock_send.return_value = _make_mock_response(status=204, method="DELETE",
                                                     body="")
        result = runner.invoke(
            cli,
            ["delete", "https://example.com/1"],
            env={"EQUINOX_DB_PATH": temp_db},
        )
        assert result.exit_code == 0, result.output

    @patch("equinox.core.client.HTTPClient.send")
    def test_post_body_from_file(self, mock_send, runner, temp_db, tmp_path):
        body_file = tmp_path / "body.json"
        body_file.write_text('{"from_file": true}', encoding="utf-8")
        mock_send.return_value = _make_mock_response(status=200, method="POST")
        result = runner.invoke(
            cli,
            ["post", "https://example.com", "--data", f"@{body_file}"],
            env={"EQUINOX_DB_PATH": temp_db},
        )
        assert result.exit_code == 0, result.output

    @patch("equinox.core.client.HTTPClient.send")
    def test_quiet_suppresses_headers(self, mock_send, runner, temp_db):
        mock_send.return_value = _make_mock_response(body='{"quiet":true}')
        result = runner.invoke(
            cli,
            ["get", "https://example.com", "--quiet"],
            env={"EQUINOX_DB_PATH": temp_db},
        )
        assert result.exit_code == 0, result.output
        assert "HTTP 200" not in result.output  # status line suppressed by --quiet

    @patch("equinox.core.client.HTTPClient.send")
    def test_no_verify_flag(self, mock_send, runner, temp_db):
        mock_send.return_value = _make_mock_response()
        result = runner.invoke(
            cli,
            ["get", "https://example.com", "--no-verify"],
            env={"EQUINOX_DB_PATH": temp_db},
        )
        assert result.exit_code == 0, result.output


# ── Request run command tests ─────────────────────────────────────────────────

class TestRequestRunCLI:
    @patch("equinox.core.client.HTTPClient.send")
    def test_run_saved_request(self, mock_send, runner, temp_db):
        mock_send.return_value = _make_mock_response()
        # Create a collection and save a request first
        runner.invoke(cli, ["collection", "create", "Test Collection"],
                      env={"EQUINOX_DB_PATH": temp_db})
        # Save a request manually via the storage layer
        from equinox.storage.database import Database
        from equinox.storage.collections import CollectionManager
        from equinox.core.request import Request
        db = Database(temp_db)
        mgr = CollectionManager(db)
        col_id = mgr.list_collections()[0]["id"]
        req = Request(method="GET", url="https://example.com", name="My Test")
        req_id = mgr.save_request(req, collection_id=col_id, name="My Test")

        result = runner.invoke(
            cli,
            ["request", "run", str(req_id)],
            env={"EQUINOX_DB_PATH": temp_db},
        )
        assert result.exit_code == 0, result.output
        assert "200" in result.output or "Running" in result.output

    def test_run_nonexistent_id(self, runner, temp_db):
        result = runner.invoke(
            cli,
            ["request", "run", "99999"],
            env={"EQUINOX_DB_PATH": temp_db},
        )
        assert result.exit_code != 0


# ── Import CLI tests ──────────────────────────────────────────────────────────

class TestImportCLI:
    def test_import_postman_file(self, runner, temp_db, tmp_path):
        postman_data = {
            "info": {
                "name": "Test Collection",
                "_postman_id": "abc123",
                "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
            },
            "item": [
                {
                    "name": "Get Users",
                    "request": {
                        "method": "GET",
                        "header": [],
                        "url": {"raw": "https://api.example.com/users"},
                    },
                }
            ],
        }
        p = tmp_path / "collection.json"
        p.write_text(json.dumps(postman_data), encoding="utf-8")
        result = runner.invoke(
            cli,
            ["import", "postman", str(p)],
            env={"EQUINOX_DB_PATH": temp_db},
        )
        assert result.exit_code == 0, result.output
        assert "collection ID" in result.output.lower() or "imported" in result.output.lower()

    def test_import_openapi_file(self, runner, temp_db, tmp_path):
        openapi_data = {
            "openapi": "3.0.0",
            "info": {"title": "Test API", "version": "1.0.0"},
            "paths": {
                "/users": {
                    "get": {
                        "operationId": "listUsers",
                        "summary": "List users",
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            },
        }
        p = tmp_path / "openapi.json"
        p.write_text(json.dumps(openapi_data), encoding="utf-8")
        result = runner.invoke(
            cli,
            ["import", "openapi", str(p)],
            env={"EQUINOX_DB_PATH": temp_db},
        )
        assert result.exit_code == 0, result.output
        assert "imported" in result.output.lower() or "collection" in result.output.lower()

    def test_import_har_file(self, runner, temp_db, tmp_path):
        har_data = {
            "log": {
                "version": "1.2",
                "title": "HAR Test",
                "creator": {"name": "test", "version": "1"},
                "entries": [
                    {
                        "startedDateTime": "2024-01-01T00:00:00Z",
                        "time": 100,
                        "request": {
                            "method": "GET",
                            "url": "https://example.com/api",
                            "headers": [],
                            "queryString": [],
                            "headersSize": -1,
                            "bodySize": -1,
                        },
                        "response": {
                            "status": 200,
                            "statusText": "OK",
                            "headers": [],
                            "content": {"size": 0, "mimeType": "application/json"},
                            "redirectURL": "",
                            "headersSize": -1,
                            "bodySize": -1,
                        },
                    }
                ],
            }
        }
        p = tmp_path / "test.har"
        p.write_text(json.dumps(har_data), encoding="utf-8")
        result = runner.invoke(
            cli,
            ["import", "har", str(p)],
            env={"EQUINOX_DB_PATH": temp_db},
        )
        assert result.exit_code == 0, result.output
        assert "imported" in result.output.lower() or "collection" in result.output.lower()

    def test_import_har_help(self, runner):
        result = runner.invoke(cli, ["import", "har", "--help"])
        assert result.exit_code == 0
        assert "HAR" in result.output or "har" in result.output.lower()

