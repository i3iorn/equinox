#!/usr/bin/env python
"""Enforce dependency source-of-truth rules.

Rules:
1) pyproject.toml is the only authoritative dependency manifest.
2) setup.py must remain a thin compatibility shim (no install_requires/extras_require).
"""

import re
import sys
from pathlib import Path


def _read(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Missing required file: {path}")
    return path.read_text(encoding="utf-8")


def check_setup_py(setup_text: str) -> list[str]:
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
            errors.append(f"setup.py should be a thin shim; found forbidden token '{token}'.")

    if "setup()" not in setup_text:
        errors.append("setup.py must call setup().")

    return errors


def check_pyproject(pyproject_text: str) -> list[str]:
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

    errors = []
    errors.extend(check_pyproject(pyproject))
    errors.extend(check_setup_py(setup_py))

    if errors:
        print("Dependency manifest consistency check FAILED:")
        for idx, err in enumerate(errors, 1):
            print(f"  {idx}. {err}")
        return 1

    print("Dependency manifest consistency check PASSED.")
    print("- pyproject.toml is authoritative")
    print("- setup.py is a compatibility shim")
    return 0


if __name__ == "__main__":
    sys.exit(main())
