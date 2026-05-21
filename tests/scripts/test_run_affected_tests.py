"""Tests for the pre-commit affected-test runner script."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "run_affected_tests.py"


@pytest.fixture(scope="module")
def affected_tests() -> object:
    spec = importlib.util.spec_from_file_location("run_affected_tests", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_pyproject(root: Path, fail_under: int = 87) -> None:
    root.joinpath("pyproject.toml").write_text(
        "\n".join(
            [
                "[tool.coverage.run]",
                'omit = ["src/equinox/plugins/*", "src/equinox/gui/*", "tests/*"]',
                "branch = true",
                "",
                "[tool.coverage.report]",
                f"fail_under = {fail_under}",
                "",
            ]
        ),
        encoding="utf-8",
    )


class TestBuildPlan:
    def test_selects_related_and_staged_tests(self, tmp_path: Path, affected_tests: object) -> None:
        _write_pyproject(tmp_path)

        source = tmp_path / "src" / "equinox" / "core" / "urls" / "parsing.py"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("# source\n", encoding="utf-8")

        related = tmp_path / "tests" / "core" / "test_urls_coverage.py"
        related.parent.mkdir(parents=True, exist_ok=True)
        related.write_text("from equinox.core.urls import normalize_url\n", encoding="utf-8")

        staged_test = tmp_path / "tests" / "core" / "test_custom_added.py"
        staged_test.write_text("def test_custom_added():\n    assert True\n", encoding="utf-8")

        unrelated = tmp_path / "tests" / "core" / "test_unrelated.py"
        unrelated.write_text("def test_unrelated():\n    assert True\n", encoding="utf-8")

        plan = affected_tests.build_plan(tmp_path, [source, staged_test])

        assert plan.source_files == (source.resolve(),)
        assert plan.staged_test_files == (staged_test.resolve(),)
        assert related.resolve() in plan.related_test_files
        assert unrelated.resolve() not in plan.selected_test_files
        assert plan.coverage_targets == (source.resolve(),)
        assert plan.coverage_threshold == 87

    def test_direct_test_candidate_is_selected(
        self, tmp_path: Path, affected_tests: object
    ) -> None:
        _write_pyproject(tmp_path)

        source = tmp_path / "src" / "equinox" / "auth" / "_basic.py"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("# source\n", encoding="utf-8")

        direct_test = tmp_path / "tests" / "auth" / "test_basic.py"
        direct_test.parent.mkdir(parents=True, exist_ok=True)
        direct_test.write_text("def test_placeholder():\n    assert True\n", encoding="utf-8")

        plan = affected_tests.build_plan(tmp_path, [source])

        assert direct_test.resolve() in plan.related_test_files
        assert plan.selected_test_files == (direct_test.resolve(),)

    def test_gui_modules_are_omitted_from_coverage_targets(
        self, tmp_path: Path, affected_tests: object
    ) -> None:
        _write_pyproject(tmp_path)

        gui_source = tmp_path / "src" / "equinox" / "gui" / "window.py"
        gui_source.parent.mkdir(parents=True, exist_ok=True)
        gui_source.write_text("# gui source\n", encoding="utf-8")

        plan = affected_tests.build_plan(tmp_path, [gui_source])

        assert plan.source_files == (gui_source.resolve(),)
        assert plan.coverage_targets == ()


class TestRunPlan:
    def test_runs_pytest_and_coverage_for_targets(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        affected_tests: object,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _write_pyproject(tmp_path)

        source = tmp_path / "src" / "equinox" / "core" / "auth_cipher.py"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("# source\n", encoding="utf-8")

        selected_test = tmp_path / "tests" / "core" / "test_crypto_key_management.py"
        selected_test.parent.mkdir(parents=True, exist_ok=True)
        selected_test.write_text("from equinox.core import auth_cipher\n", encoding="utf-8")

        plan = affected_tests.build_plan(tmp_path, [source])

        calls = []

        def fake_run(command, cwd=None, env=None, check=None, text=None, capture_output=None):
            calls.append((list(command), Path(cwd), dict(env or {}), check, text, capture_output))
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        monkeypatch.setattr(affected_tests.subprocess, "run", fake_run)

        exit_code = affected_tests.run_plan(tmp_path, plan)

        assert exit_code == 0
        assert len(calls) == 2
        assert (
            calls[0][0][:5]
            == [
                affected_tests.sys.executable,
                "-m",
                "coverage",
                "run",
                "-m",
            ][:5]
        )
        assert calls[0][0][5] == "pytest"
        assert calls[1][0][:4] == [
            affected_tests.sys.executable,
            "-m",
            "coverage",
            "report",
        ]
        assert (
            "--include=" + selected_test.resolve().relative_to(tmp_path).as_posix()
            not in calls[1][0]
        )
        assert any(arg.startswith("--include=") for arg in calls[1][0])
        assert any(arg.startswith("--fail-under=87") for arg in calls[1][0])
        assert calls[0][1] == tmp_path
        assert calls[1][1] == tmp_path
        assert calls[0][3] is False
        assert calls[1][3] is False
        assert calls[0][4] is True and calls[0][5] is True
        assert calls[1][4] is True and calls[1][5] is True
        assert calls[0][2]["PYTHONPATH"].startswith(str((tmp_path / "src").resolve()))
        assert calls[0][2]["COVERAGE_FILE"].startswith(str(tmp_path))
        assert capsys.readouterr().out == ""

    def test_skips_coverage_when_all_targets_are_omitted(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        affected_tests: object,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _write_pyproject(tmp_path)

        gui_source = tmp_path / "src" / "equinox" / "gui" / "window.py"
        gui_source.parent.mkdir(parents=True, exist_ok=True)
        gui_source.write_text("# gui source\n", encoding="utf-8")

        gui_test = tmp_path / "tests" / "gui" / "test_window.py"
        gui_test.parent.mkdir(parents=True, exist_ok=True)
        gui_test.write_text("def test_gui():\n    assert True\n", encoding="utf-8")

        plan = affected_tests.build_plan(tmp_path, [gui_source])
        assert plan.coverage_targets == ()

        calls = []

        def fake_run(command, cwd=None, env=None, check=None, text=None, capture_output=None):
            calls.append((list(command), Path(cwd), dict(env or {}), check, text, capture_output))
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        monkeypatch.setattr(affected_tests.subprocess, "run", fake_run)

        exit_code = affected_tests.run_plan(tmp_path, plan)

        assert exit_code == 0
        assert len(calls) == 1
        assert calls[0][0][:3] == [affected_tests.sys.executable, "-m", "pytest"]
        assert calls[0][3] is False
        assert calls[0][4] is True and calls[0][5] is True
        assert capsys.readouterr().out == ""

    def test_prints_formatted_output_on_test_failure(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        affected_tests: object,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _write_pyproject(tmp_path)

        source = tmp_path / "src" / "equinox" / "core" / "auth_cipher.py"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("# source\n", encoding="utf-8")

        selected_test = tmp_path / "tests" / "core" / "test_crypto_key_management.py"
        selected_test.parent.mkdir(parents=True, exist_ok=True)
        selected_test.write_text("from equinox.core import auth_cipher\n", encoding="utf-8")

        plan = affected_tests.build_plan(tmp_path, [source])

        def fake_run(command, cwd=None, env=None, check=None, text=None, capture_output=None):
            return subprocess.CompletedProcess(
                command,
                2,
                stdout="FAILED test_demo.py::test_x",
                stderr="traceback details",
            )

        monkeypatch.setattr(affected_tests.subprocess, "run", fake_run)

        exit_code = affected_tests.run_plan(tmp_path, plan)
        output = capsys.readouterr().out

        assert exit_code == 2
        assert "[affected-tests] ERROR: Test execution failed (exit=2)" in output
        assert "[affected-tests] --- stdout ---" in output
        assert "FAILED test_demo.py::test_x" in output
        assert "[affected-tests] --- stderr ---" in output
        assert "traceback details" in output
