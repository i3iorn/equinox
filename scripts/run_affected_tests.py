#!/usr/bin/env python3
"""
Fast pre-commit test runner with dynamic per-commit coverage.

Behavior:
- Only consider files passed in by pre-commit (pass_filenames=true).
- If a changed file is under src/equinox/, map it to tests/<same dirs>/test_<name>.py.
- If a changed file is itself a test, run it directly.
- Coverage is restricted ONLY to the affected source files.
- A temporary .coveragerc is generated automatically.
"""
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src" / "equinox"
TEST_ROOT = REPO_ROOT / "tests"


def map_source_to_test(path: Path) -> Path | None:
    """Map src/equinox/foo/bar.py → tests/foo/test_bar.py."""
    try:
        rel = path.relative_to(SRC_ROOT)
    except ValueError:
        return None

    if rel.name == "__init__.py":
        return None

    test_path = TEST_ROOT / rel.parent / f"test_{rel.stem.lstrip('_')}.py"
    return test_path if test_path.exists() else None


def map_test_to_source(path: Path) -> Path | None:
    """Map tests/foo/test_bar.py → src/equinox/foo/bar.py."""
    try:
        rel = path.relative_to(TEST_ROOT)
    except ValueError:
        return None

    if not rel.name.startswith("test_"):
        return None

    src_name = rel.name.replace("test_", "", 1)
    src_path = SRC_ROOT / rel.parent / src_name
    return src_path if src_path.exists() else None


def main():
    # Pre-commit passes filenames as arguments
    changed = [REPO_ROOT / p for p in sys.argv[1:]]

    tests = []
    sources = []

    for p in changed:
        # If it's a test file
        if p.is_relative_to(TEST_ROOT) and p.name.startswith("test_"):
            tests.append(p)
            src = map_test_to_source(p)
            if src:
                sources.append(src)
            continue

        # If it's a source file
        if p.is_relative_to(SRC_ROOT):
            src = p
            sources.append(src)
            t = map_source_to_test(p)
            if t:
                tests.append(t)

    tests = sorted(set(tests))
    sources = sorted(set(sources))

    if not tests:
        print("[affected-tests] No matching tests found for changed files")
        return 0

    print(f"[affected-tests] Running {len(tests)} test(s):")
    for t in tests:
        print("  -", t.relative_to(REPO_ROOT))

    print(f"[affected-tests] Restricting coverage to {len(sources)} file(s):")
    for s in sources:
        print("  -", s.relative_to(REPO_ROOT))

    # Build temporary coverage config
    with tempfile.NamedTemporaryFile("w", delete=False) as tmp:
        tmp.write("[run]\n")
        tmp.write("include =\n")
        for s in sources:
            tmp.write(f"    {s}\n")
        coveragerc_path = tmp.name

    test_paths = [str(t.relative_to(REPO_ROOT)) for t in tests]

    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "--cov",
        f"--cov-config={coveragerc_path}",
        "--cov-report=term-missing",
    ] + test_paths

    result = subprocess.run(cmd, cwd=REPO_ROOT)
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
