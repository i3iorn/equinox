"""Tests for CLI import commands (Postman, OpenAPI, HAR)."""

import json
import pytest
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from equinox.storage.database import Database


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "cli_import.db"))


@pytest.fixture
def runner():
    return CliRunner()


def _patch_db(db):
    return patch("equinox.cli.main.get_db", return_value=db)


def _write_json(tmp_path, name, data):
    p = tmp_path / name
    p.write_text(json.dumps(data), encoding="utf-8")
    return str(p)


def _minimal_postman(tmp_path):
    data = {
        "info": {
            "name": "CLI Test Collection",
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
            "_postman_id": "test-id",
            "description": "A test collection",
        },
        "item": [
            {
                "name": "Get Users",
                "request": {
                    "method": "GET",
                    "url": "https://api.example.com/users",
                    "header": [],
                },
            }
        ],
    }
    return _write_json(tmp_path, "postman.json", data)


def _minimal_openapi(tmp_path):
    data = {
        "openapi": "3.0.0",
        "info": {"title": "Test API", "version": "1.0.0", "description": "Test desc"},
        "paths": {
            "/users": {
                "get": {
                    "summary": "List users",
                    "operationId": "listUsers",
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }
    return _write_json(tmp_path, "openapi.json", data)


def _minimal_har(tmp_path):
    data = {
        "log": {
            "version": "1.2",
            "creator": {"name": "test", "version": "1.0"},
            "entries": [
                {
                    "request": {
                        "method": "GET",
                        "url": "https://example.com/api/test",
                        "httpVersion": "HTTP/1.1",
                        "headers": [],
                        "queryString": [],
                        "cookies": [],
                        "headersSize": -1,
                        "bodySize": 0,
                    },
                    "response": {
                        "status": 200,
                        "statusText": "OK",
                        "httpVersion": "HTTP/1.1",
                        "headers": [],
                        "cookies": [],
                        "content": {"size": 0, "mimeType": "text/plain"},
                        "redirectURL": "",
                        "headersSize": -1,
                        "bodySize": 0,
                    },
                    "cache": {},
                    "timings": {"send": 0, "wait": 0, "receive": 0},
                }
            ],
        }
    }
    return _write_json(tmp_path, "archive.har", data)


# ── Postman import ────────────────────────────────────────────────────────


class TestImportPostman:
    def test_import_postman(self, runner, db, tmp_path):
        from equinox.cli.imports import import_cmd

        path = _minimal_postman(tmp_path)
        with _patch_db(db):
            result = runner.invoke(import_cmd, ["postman", path])
        assert result.exit_code == 0
        assert "Successfully imported" in result.output

    def test_import_postman_preview(self, runner, tmp_path):
        from equinox.cli.imports import import_cmd

        path = _minimal_postman(tmp_path)
        result = runner.invoke(import_cmd, ["postman", path, "--preview"])
        assert result.exit_code == 0
        assert "CLI Test Collection" in result.output
        assert "Requests:" in result.output


# ── OpenAPI import ────────────────────────────────────────────────────────


class TestImportOpenAPI:
    def test_import_openapi(self, runner, db, tmp_path):
        from equinox.cli.imports import import_cmd

        path = _minimal_openapi(tmp_path)
        with _patch_db(db):
            result = runner.invoke(import_cmd, ["openapi", path])
        assert result.exit_code == 0
        assert "Successfully imported" in result.output

    def test_import_openapi_preview(self, runner, tmp_path):
        from equinox.cli.imports import import_cmd

        path = _minimal_openapi(tmp_path)
        result = runner.invoke(import_cmd, ["openapi", path, "--preview"])
        assert result.exit_code == 0
        assert "Test API" in result.output
        assert "Paths:" in result.output


# ── HAR import ────────────────────────────────────────────────────────────


class TestImportHAR:
    def test_import_har(self, runner, db, tmp_path):
        from equinox.cli.imports import import_cmd

        path = _minimal_har(tmp_path)
        with _patch_db(db):
            result = runner.invoke(import_cmd, ["har", path])
        assert result.exit_code == 0
        assert "Successfully imported" in result.output

