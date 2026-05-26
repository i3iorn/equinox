#!/usr/bin/env python
"""Run tests impacted by staged source changes.

The script is intended for pre-commit use. It:
1. inspects the staged file list (or uses explicit paths passed on the CLI),
2. identifies changed source modules under ``src/equinox``,
3. finds test files in ``tests/`` that are relevant to those modules,
4. runs the selected tests, and
5. verifies coverage for the changed non-omitted source files against the
   configured ``fail_under`` threshold from ``pyproject.toml``.

Usage:
    python scripts/run_affected_tests.py [paths...]

When no paths are provided, the script falls back to the staged files reported
by ``git diff --cached``.
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class TestRunPlan:
    """Execution plan derived from changed files."""

    changed_files: Tuple[Path, ...]
    source_files: Tuple[Path, ...]
    staged_test_files: Tuple[Path, ...]
    related_test_files: Tuple[Path, ...]
    coverage_targets: Tuple[Path, ...]
    coverage_threshold: int
    omit_patterns: Tuple[str, ...]

    @property
    def selected_test_files(self) -> Tuple[Path, ...]:
        """Return the full deduplicated test file set to execute."""
        ordered: Dict[Path, None] = {}
        for path in self.staged_test_files + self.related_test_files:
            ordered.setdefault(path, None)
        return tuple(ordered.keys())


_COVERAGE_FAIL_UNDER_RE = re.compile(r"^\s*fail_under\s*=\s*(\d+)\s*$", re.MULTILINE)
_COVERAGE_OMIT_BLOCK_RE = re.compile(r"\[tool\.coverage\.run](.*?)(?:\n\[[^]]+]|\Z)", re.DOTALL)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _normalize_changed_paths(repo_root: Path, items: Sequence[str]) -> Tuple[Path, ...]:
    normalized: Dict[Path, None] = {}
    for raw in items:
        path = Path(raw)
        if not path.is_absolute():
            path = repo_root / path
        path = path.resolve()
        if path.exists():
            normalized.setdefault(path, None)
    return tuple(normalized.keys())


def _staged_files(repo_root: Path) -> Tuple[Path, ...]:
    completed = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMRTUXB"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    files = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    return _normalize_changed_paths(repo_root, files)


def _is_source_file(path: Path, repo_root: Path) -> bool:
    try:
        relative = path.resolve().relative_to((repo_root / "src" / "equinox").resolve())
    except ValueError:
        return False
    return relative.suffix == ".py"


def _is_test_file(path: Path, repo_root: Path) -> bool:
    try:
        relative = path.resolve().relative_to((repo_root / "tests").resolve())
    except ValueError:
        return False
    return relative.suffix == ".py" and relative.name.startswith("test_")


def _load_coverage_settings(repo_root: Path) -> Tuple[int, Tuple[str, ...]]:
    pyproject_path = repo_root / "pyproject.toml"
    if not pyproject_path.exists():
        raise FileNotFoundError(f"Missing required file: {pyproject_path}")

    text = pyproject_path.read_text(encoding="utf-8")
    match = _COVERAGE_FAIL_UNDER_RE.search(text)
    if not match:
        raise RuntimeError("Could not determine coverage fail_under from pyproject.toml")
    fail_under = int(match.group(1))

    omit_patterns: List[str] = []
    block = _COVERAGE_OMIT_BLOCK_RE.search(text)
    if block:
        omit_patterns = re.findall(r'"([^"]+)"', block.group(1))

    return fail_under, tuple(omit_patterns)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def _module_parts(source_path: Path, repo_root: Path) -> Tuple[str, ...]:
    relative = source_path.resolve().relative_to((repo_root / "src" / "equinox").resolve())
    return relative.with_suffix("").parts


def _module_prefixes(source_path: Path, repo_root: Path) -> Tuple[str, ...]:
    parts = _module_parts(source_path, repo_root)
    prefixes: List[str] = []
    for end in range(len(parts), 0, -1):
        prefixes.append("equinox." + ".".join(parts[:end]))
    return tuple(prefixes)


def _significant_tokens(source_path: Path, repo_root: Path) -> Tuple[str, ...]:
    parts = list(_module_parts(source_path, repo_root))
    parts.append(source_path.stem)
    tokens: List[str] = []
    ignored = {"src", "equinox", "tests", "test", "__init__", "__pycache__", "core"}
    for part in parts:
        for token in re.split(r"[^a-z0-9]+", part.lower()):
            if token and token not in ignored and token not in tokens:
                tokens.append(token)
    return tuple(tokens)


def _direct_test_candidate(source_path: Path, repo_root: Path) -> Optional[Path]:
    relative = source_path.resolve().relative_to((repo_root / "src" / "equinox").resolve())
    if relative.name == "__init__.py":
        return None
    candidate = (repo_root / "tests" / relative).with_name(f"test_{relative.stem.lstrip('_')}.py")
    if candidate.exists():
        return candidate.resolve()
    return None


def _discover_test_files(repo_root: Path) -> Tuple[Path, ...]:
    tests_dir = repo_root / "tests"
    if not tests_dir.exists():
        return tuple()
    discovered = [path.resolve() for path in tests_dir.rglob("test_*.py") if path.is_file()]
    return tuple(sorted(discovered))


def _test_relevance_score(
    source_path: Path,
    test_path: Path,
    test_text: str,
    repo_root: Path,
    debug: bool,
) -> Tuple[int, List[str]]:
    score = 0
    reasons: List[str] = []

    direct_candidate = _direct_test_candidate(source_path, repo_root)
    if direct_candidate is not None and test_path.resolve() == direct_candidate:
        if debug:
            print(f"[affected-tests] direct match: {test_path.name} ← {source_path.name}")
        return 100, ["direct"]

    prefixes = _module_prefixes(source_path, repo_root)
    if any(prefix in test_text for prefix in prefixes):
        score += 4
        reasons.append("module-prefix")

    tokens = _significant_tokens(source_path, repo_root)
    haystack = f"{test_path.stem.lower()} {test_path.as_posix().lower()}"
    if any(token in haystack for token in tokens):
        score += 2
        reasons.append("token-match")

    if test_path.parent.name == source_path.parent.name:
        score += 1
        reasons.append("same-directory")

    if debug and score > 0:
        print(
            f"[affected-tests] score={score:2d} for {test_path.name} "
            f"← {source_path.name} ({', '.join(reasons)})"
        )

    return score, reasons


def _select_related_tests(
    repo_root: Path,
    source_files: Sequence[Path],
    all_test_files: Sequence[Path],
    debug: bool,
) -> Tuple[Path, ...]:
    if not source_files:
        return tuple()

    cached_test_text = {path: _read_text(path) for path in all_test_files}
    selected: Dict[Path, None] = {}

    for source_path in source_files:
        if debug:
            print(f"[affected-tests] analyzing source: {source_path.name}")

        matched_count = 0

        for test_path in all_test_files:
            score, reasons = _test_relevance_score(
                source_path,
                test_path,
                cached_test_text[test_path],
                repo_root,
                debug=debug,
            )

            if score >= 4:
                selected.setdefault(test_path, None)
                matched_count += 1

        if debug:
            print(f"[affected-tests] → matched {matched_count} tests for {source_path.name}")

    if debug:
        print(f"[affected-tests] total selected tests: {len(selected)}")

    return tuple(sorted(selected.keys()))


def _coverage_targets(
    repo_root: Path, source_files: Sequence[Path], omit_patterns: Sequence[str]
) -> Tuple[Path, ...]:
    targets: Dict[Path, None] = {}
    for source_path in source_files:
        relative = source_path.resolve().relative_to(repo_root.resolve())
        rel_posix = relative.as_posix()
        if any(fnmatch.fnmatchcase(rel_posix, pattern) for pattern in omit_patterns):
            continue
        targets.setdefault(source_path.resolve(), None)
    return tuple(sorted(targets.keys()))


def build_plan(repo_root: Path, changed_files: Sequence[Path], debug: bool = False) -> TestRunPlan:
    fail_under, omit_patterns = _load_coverage_settings(repo_root)

    source_files: List[Path] = []
    staged_test_files: List[Path] = []
    for path in changed_files:
        if _is_source_file(path, repo_root):
            source_files.append(path.resolve())
        elif _is_test_file(path, repo_root):
            staged_test_files.append(path.resolve())

    selected_tests = _select_related_tests(
        repo_root, source_files, _discover_test_files(repo_root), debug
    )
    coverage_targets = _coverage_targets(repo_root, source_files, omit_patterns)

    return TestRunPlan(
        changed_files=tuple(path.resolve() for path in changed_files),
        source_files=tuple(source_files),
        staged_test_files=tuple(staged_test_files),
        related_test_files=selected_tests,
        coverage_targets=coverage_targets,
        coverage_threshold=fail_under,
        omit_patterns=omit_patterns,
    )


def _build_pythonpath(repo_root: Path) -> str:
    src_dir = (repo_root / "src").resolve()
    existing = os.environ.get("PYTHONPATH")
    if existing:
        return str(src_dir) + os.pathsep + existing
    return str(src_dir)


def _format_command(command: Sequence[str]) -> str:
    return " ".join(command)


def _print_failure(header: str, lines: Sequence[str] | None = None) -> None:
    print(f"[affected-tests] ERROR: {header}")
    for line in lines or ():
        print(f"[affected-tests] {line}")


def _run_subprocess(
    command: Sequence[str],
    repo_root: Path,
    env: Dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=repo_root,
        env=env,
        check=False,
        text=True,
        capture_output=True,
    )


def _emit_failed_command(
    step: str,
    command: Sequence[str],
    result: subprocess.CompletedProcess[str],
) -> int:
    _print_failure(
        f"{step} failed (exit={result.returncode})",
        [f"command: {_format_command(command)}"],
    )
    if result.stdout and result.stdout.strip():
        print("[affected-tests] --- stdout ---")
        print(result.stdout.strip())
    if result.stderr and result.stderr.strip():
        print("[affected-tests] --- stderr ---")
        print(result.stderr.strip())
    return result.returncode or 1


def run_plan(repo_root: Path, plan: TestRunPlan, debug: bool = False) -> int:
    selected_tests = plan.selected_test_files
    if not selected_tests:
        if plan.source_files:
            _print_failure(
                "No relevant test files found for changed modules",
                [f"source: {path.relative_to(repo_root).as_posix()}" for path in plan.source_files],
            )
            return 1
        return 0

    if not debug:
        total = len(selected_tests)
        print(f"[affected-tests] selected {total} test(s) to run")

    relative_tests = [path.relative_to(repo_root).as_posix() for path in selected_tests]
    relative_targets = [path.relative_to(repo_root).as_posix() for path in plan.coverage_targets]

    env = os.environ.copy()
    env["PYTHONPATH"] = _build_pythonpath(repo_root)

    coverage_file = repo_root / f".coverage.precommit-{os.getpid()}"
    env["COVERAGE_FILE"] = str(coverage_file)

    try:
        if relative_targets:
            run_cmd = [sys.executable, "-m", "coverage", "run", "-m", "pytest", *relative_tests]
            run_result = _run_subprocess(run_cmd, repo_root, env)
            if run_result.returncode != 0:
                return _emit_failed_command("Test execution", run_cmd, run_result)

            include_arg = ",".join(relative_targets)
            report_cmd = [
                sys.executable,
                "-m",
                "coverage",
                "report",
                f"--include={include_arg}",
                f"--fail-under={plan.coverage_threshold}",
            ]
            report_result = _run_subprocess(report_cmd, repo_root, env)
            if report_result.returncode != 0:
                return _emit_failed_command(
                    "Coverage threshold check",
                    report_cmd,
                    report_result,
                )
        else:
            test_cmd = [sys.executable, "-m", "pytest", *relative_tests]
            test_result = _run_subprocess(test_cmd, repo_root, env)
            if test_result.returncode != 0:
                return _emit_failed_command("Test execution", test_cmd, test_result)
    finally:
        try:
            coverage_file.unlink()
        except FileNotFoundError:
            pass

    return 0


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run tests affected by staged source changes")
    parser.add_argument(
        "paths",
        nargs="*",
        help="Changed file paths. When omitted, the script inspects staged git changes.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print detailed test matching diagnostics",
    )

    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None, repo_root: Optional[Path] = None) -> int:
    args = _parse_args(argv)
    root = repo_root or _repo_root()

    if args.paths:
        changed_files = _normalize_changed_paths(root, args.paths)
    else:
        changed_files = _staged_files(root)

    plan = build_plan(root, changed_files, debug=args.debug)
    return run_plan(root, plan, debug=args.debug)


if __name__ == "__main__":
    sys.exit(main())
