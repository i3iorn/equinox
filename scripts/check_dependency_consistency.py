#!/usr/bin/env python
"""Enforce dependency source-of-truth rules.

Rules:
1) pyproject.toml is the only authoritative dependency manifest.
2) setup.py must remain a thin compatibility shim (no install_requires/extras_require).
3) requirements.txt must delegate to the generated lockfile and editable local install.
4) requirements-lock.txt must exist and be committed.
"""

import re
import subprocess
import sys
from pathlib import Path


def _read(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Missing required file: {path}")
    return path.read_text(encoding="utf-8")


def _non_comment_lines(text: str) -> list[str]:
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        lines.append(stripped)
    return lines


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


def check_requirements_txt(requirements_text: str) -> list[str]:
    errors = []
    non_comment = _non_comment_lines(requirements_text)
    expected = ["-r requirements-lock.txt", "-e ."]

    if not non_comment:
        errors.append("requirements.txt must delegate to requirements-lock.txt and '-e .'.")
        return errors

    if non_comment != expected:
        errors.append(
            "requirements.txt must contain exactly two non-comment entries: '-r requirements-lock.txt' and '-e .'."
        )

    return errors


def check_requirements_lock(lock_text: str) -> list[str]:
    errors = []
    if not lock_text.strip():
        errors.append("requirements-lock.txt must not be empty.")
    if "#" not in lock_text:
        errors.append(
            "requirements-lock.txt should look like a generated lockfile with header comments."
        )
    return errors


def is_git_tracked(root: Path, relative_path: str) -> bool:
    """Return True when the path is tracked by git, or when git metadata is unavailable."""
    if not (root / ".git").exists():
        return True

    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", relative_path],
        cwd=str(root),
        text=True,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


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
    requirements = _read(root / "requirements.txt")
    requirements_lock = _read(root / "requirements-lock.txt")

    errors = []
    errors.extend(check_pyproject(pyproject))
    errors.extend(check_setup_py(setup_py))
    errors.extend(check_requirements_txt(requirements))
    errors.extend(check_requirements_lock(requirements_lock))
    if not is_git_tracked(root, "requirements-lock.txt"):
        errors.append(
            "requirements-lock.txt must be git-tracked (committed) for reproducible installs."
        )

    if errors:
        print("Dependency manifest consistency check FAILED:")
        for idx, err in enumerate(errors, 1):
            print(f"  {idx}. {err}")
        return 1

    print("Dependency manifest consistency check PASSED.")
    print("- pyproject.toml is authoritative")
    print("- setup.py is a compatibility shim")
    print("- requirements.txt delegates to requirements-lock.txt and editable install")
    print("- requirements-lock.txt exists and is git-tracked")
    return 0


if __name__ == "__main__":
    sys.exit(main())
