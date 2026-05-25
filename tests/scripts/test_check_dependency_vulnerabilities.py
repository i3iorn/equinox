"""Tests for dependency vulnerability scanning script."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "check_dependency_vulnerabilities.py"


@pytest.fixture(scope="module")
def vulnerability_script() -> object:
    spec = importlib.util.spec_from_file_location("check_dependency_vulnerabilities", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_extract_json_payload_uses_last_json_line(vulnerability_script: object) -> None:
    raw = "Found vulnerabilities\n{\"dependencies\": []}\n"
    assert vulnerability_script._extract_json_payload(raw) == "{\"dependencies\": []}"


def test_parse_findings_formats_fix_versions(vulnerability_script: object) -> None:
    payload = {
        "dependencies": [
            {
                "name": "cryptography",
                "version": "42.0.8",
                "vulns": [
                    {"id": "CVE-1", "fix_versions": ["46.0.6"]},
                    {"id": "CVE-2", "fix_versions": []},
                ],
            }
        ]
    }

    findings = vulnerability_script._parse_findings(payload)

    assert findings == [
        ("cryptography", "42.0.8", "CVE-1", "46.0.6"),
        ("cryptography", "42.0.8", "CVE-2", "none listed"),
    ]


def test_run_scan_fails_when_lockfile_missing(
    vulnerability_script: object,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    missing_lock = tmp_path / "requirements-lock.txt"
    monkeypatch.setattr(vulnerability_script, "LOCK_PATH", missing_lock)

    assert vulnerability_script.run_scan() == 1


def test_run_scan_passes_without_findings(
    vulnerability_script: object,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    lock_path = tmp_path / "requirements-lock.txt"
    lock_path.write_text("# lock\n", encoding="utf-8")

    monkeypatch.setattr(vulnerability_script, "LOCK_PATH", lock_path)
    monkeypatch.setattr(vulnerability_script, "ROOT", tmp_path)

    def fake_run(command, cwd=None, text=None, capture_output=None, check=None):
        assert str(lock_path) in command
        assert cwd == str(tmp_path)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout='{"dependencies": [{"name": "demo", "version": "1.0.0", "vulns": []}]}',
            stderr="",
        )

    monkeypatch.setattr(vulnerability_script.subprocess, "run", fake_run)

    assert vulnerability_script.run_scan() == 0
    assert "PASSED" in capsys.readouterr().out


def test_run_scan_fails_on_vulnerabilities(
    vulnerability_script: object,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    lock_path = tmp_path / "requirements-lock.txt"
    lock_path.write_text("# lock\n", encoding="utf-8")

    monkeypatch.setattr(vulnerability_script, "LOCK_PATH", lock_path)
    monkeypatch.setattr(vulnerability_script, "ROOT", tmp_path)

    def fake_run(command, cwd=None, text=None, capture_output=None, check=None):
        return subprocess.CompletedProcess(
            command,
            1,
            stdout=(
                '{"dependencies": ['
                '{"name": "cryptography", "version": "42.0.8", "vulns": ['
                '{"id": "CVE-2026-26007", "fix_versions": ["46.0.6"]}'
                ']}]}'
            ),
            stderr="Found vulnerabilities",
        )

    monkeypatch.setattr(vulnerability_script.subprocess, "run", fake_run)

    assert vulnerability_script.run_scan() == 1
    output = capsys.readouterr().out
    assert "known vulnerabilities detected" in output
    assert "cryptography==42.0.8: CVE-2026-26007" in output

