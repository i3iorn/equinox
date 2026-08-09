"""Tests for dependency consistency helper checks."""

from __future__ import annotations

import importlib.util
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


def test_check_pyproject_passes_on_valid_manifest(consistency_script: object) -> None:
    text = """
[project]
dependencies = [
    "httpx",
]

[project.optional-dependencies]
dev = [
    "pytest",
]
"""
    assert consistency_script.check_pyproject(text) == []


def test_check_pyproject_flags_missing_sections(consistency_script: object) -> None:
    errors = consistency_script.check_pyproject("")
    assert len(errors) == 4


def test_check_setup_py_passes_on_thin_shim(consistency_script: object) -> None:
    text = "from setuptools import setup\n\nsetup()\n"
    assert consistency_script.check_setup_py(text) == []


def test_check_setup_py_flags_forbidden_tokens(consistency_script: object) -> None:
    text = 'from setuptools import setup\n\nsetup(name="equinox", install_requires=[])\n'
    errors = consistency_script.check_setup_py(text)
    assert any("install_requires" in err for err in errors)
    assert any("name=" in err for err in errors)
