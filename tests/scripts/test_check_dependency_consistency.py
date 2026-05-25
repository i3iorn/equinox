"""Tests for dependency consistency helper checks."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "check_dependency_consistency.py"


@pytest.fixture(scope="module")
def consistency_script() -> object:
    spec = importlib.util.spec_from_file_location("check_dependency_consistency", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_is_git_tracked_skips_check_outside_git_repo(
    consistency_script: object,
    tmp_path: Path,
) -> None:
    assert consistency_script.is_git_tracked(tmp_path, "requirements-lock.txt") is True


def test_is_git_tracked_returns_false_for_untracked_file(
    consistency_script: object,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (tmp_path / ".git").mkdir()

    def fake_run(command, cwd=None, text=None, capture_output=None, check=None):
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="")

    monkeypatch.setattr(consistency_script.subprocess, "run", fake_run)

    assert consistency_script.is_git_tracked(tmp_path, "requirements-lock.txt") is False


def test_is_git_tracked_returns_true_for_tracked_file(
    consistency_script: object,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (tmp_path / ".git").mkdir()

    def fake_run(command, cwd=None, text=None, capture_output=None, check=None):
        return subprocess.CompletedProcess(command, 0, stdout="requirements-lock.txt\n", stderr="")

    monkeypatch.setattr(consistency_script.subprocess, "run", fake_run)

    assert consistency_script.is_git_tracked(tmp_path, "requirements-lock.txt") is True

