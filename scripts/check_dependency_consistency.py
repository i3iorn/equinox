#!/usr/bin/env python
"""Enforce dependency source-of-truth rules.

Rules:
1) pyproject.toml is the only authoritative dependency manifest.
2) setup.py must remain a thin compatibility shim (no install_requires/extras_require).
3) requirements.txt must delegate to pyproject extras via '-e .[dev]'.
"""

import re
import sys
from pathlib import Path
from typing import List


def _read(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Missing required file: {path}")
    return path.read_text(encoding="utf-8")


def _non_comment_lines(text: str) -> List[str]:
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        lines.append(stripped)
    return lines


def check_setup_py(setup_text: str) -> List[str]:
    errors = []
    forbidden = [
        "install_requires",
        "extras_require",
        "python_requires",
        "entry_points",
        "version=",
        "name=",
    ]
    for token in forbidden:
        if token in setup_text:
            errors.append(
                f"setup.py should be a thin shim; found forbidden token '{token}'."
            )

    if "setup()" not in setup_text:
        errors.append("setup.py must call setup().")

    return errors


def check_requirements_txt(requirements_text: str) -> List[str]:
    errors = []
    non_comment = _non_comment_lines(requirements_text)
    expected = "-e .[dev]"

    if not non_comment:
        errors.append("requirements.txt must contain '-e .[dev]'.")
        return errors

    if len(non_comment) != 1 or non_comment[0] != expected:
        errors.append(
            "requirements.txt must contain only one non-comment entry: '-e .[dev]'."
        )

    return errors


def check_pyproject(pyproject_text: str) -> List[str]:
    errors = []
    if "[project]" not in pyproject_text:
        errors.append("pyproject.toml is missing [project] section.")
    if "dependencies = [" not in pyproject_text:
        errors.append("pyproject.toml is missing project.dependencies.")
    if "[project.optional-dependencies]" not in pyproject_text:
        errors.append("pyproject.toml is missing [project.optional-dependencies] section.")
    if not re.search(r"\bdev\s*=\s*\[", pyproject_text):
        errors.append("pyproject.toml is missing optional dependency group 'dev'.")
    return errors


def main() -> int:
    root = Path(__file__).resolve().parents[1]

    pyproject = _read(root / "pyproject.toml")
    setup_py = _read(root / "setup.py")
    requirements = _read(root / "requirements.txt")

    errors = []
    errors.extend(check_pyproject(pyproject))
    errors.extend(check_setup_py(setup_py))
    errors.extend(check_requirements_txt(requirements))

    if errors:
        print("Dependency manifest consistency check FAILED:")
        for idx, err in enumerate(errors, 1):
            print(f"  {idx}. {err}")
        return 1

    print("Dependency manifest consistency check PASSED.")
    print("- pyproject.toml is authoritative")
    print("- setup.py is a compatibility shim")
    print("- requirements.txt delegates to pyproject extras")
    return 0


if __name__ == "__main__":
    sys.exit(main())

