#!/usr/bin/env python3
"""
Fast pre-commit test runner.

Rules:
- Only consider staged files.
- If a changed file is under src/equinox/, map it to tests/<same dirs>/test_<name>.py.
- If a changed file is itself a test, run it directly.
- No fuzzy matching, no scanning entire test suite, no coverage.
"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src" / "equinox"
TEST_ROOT = REPO_ROOT / "tests"


def staged_files():
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMRTUXB"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [REPO_ROOT / p for p in out.stdout.split()]


def map_source_to_test(path: Path) -> Path | None:
    try:
        rel = path.relative_to(SRC_ROOT)
    except ValueError:
        return None

    if rel.name == "__init__.py":
        return None

    test_path = TEST_ROOT / rel.parent / f"test_{rel.stem.lstrip('_')}.py"
    return test_path if test_path.exists() else None


def main():
    changed = staged_files()
    tests = []

    for p in changed:
        if p.is_relative_to(TEST_ROOT) and p.name.startswith("test_"):
            tests.append(p)
            continue

        t = map_source_to_test(p)
        if t:
            tests.append(t)

    tests = sorted(set(tests))

    if not tests:
        print("[affected-tests] No matching tests found for staged changes")
        return 0

    print(f"[affected-tests] Running {len(tests)} test(s):")
    for t in tests:
        print("  -", t.relative_to(REPO_ROOT))

    cmd = [sys.executable, "-m", "pytest"] + [str(t.relative_to(REPO_ROOT)) for t in tests]

    result = subprocess.run(cmd, cwd=REPO_ROOT)
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
