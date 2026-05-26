#!/usr/bin/env python3
import ast
import subprocess
import sys
from pathlib import Path

_LIMITS = {
    "module": 1000,
    "function": 60,
    "class": 800,
}


def analyze_file(path: Path):
    with path.open("r", encoding="utf-8-sig") as f:
        source = f.read()

    tree = ast.parse(source)
    module_lines = len(source.splitlines())

    classes = []
    functions = []

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            class_info = {
                "name": node.name,
                "lineno": node.lineno,
                "lines": node.end_lineno - node.lineno + 1,
                "methods": [],
            }

            for item in node.body:
                if isinstance(item, ast.FunctionDef):
                    class_info["methods"].append(
                        {
                            "name": item.name,
                            "lines": item.end_lineno - item.lineno + 1,
                            "lineno": item.lineno,
                        }
                    )

            classes.append(class_info)

        elif isinstance(node, ast.FunctionDef):
            functions.append(
                {
                    "module": path.stem,
                    "name": node.name,
                    "lines": node.end_lineno - node.lineno + 1,
                    "lineno": node.lineno,
                }
            )

    return {
        "module": path,
        "module_lines": module_lines,
        "classes": classes,
        "functions": functions,
    }


def get_staged_python_files():
    """Fallback when run manually without filenames."""
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
        capture_output=True,
        text=True,
    )
    files = [Path(f) for f in result.stdout.splitlines() if f.endswith(".py")]
    return [f for f in files if f.exists()]


def check_size_report(path: Path, report: dict):
    violations = []

    # Module-level
    if report["module_lines"] > _LIMITS["module"]:
        over = (report["module_lines"] - _LIMITS["module"]) / _LIMITS["module"] * 100
        violations.append(
            (
                over,
                f"{path}:1: Module too large - {report['module_lines']} lines ({over:.1f}% over)",
            )
        )

    # Classes + methods
    for cls in report["classes"]:
        if cls["lines"] > _LIMITS["class"]:
            over = (cls["lines"] - _LIMITS["class"]) / _LIMITS["class"] * 100
            violations.append(
                (
                    over,
                    f"{path}:{cls['lineno']}: Class too large: {cls['name']} - {cls['lines']} lines ({over:.1f}% over)",
                )
            )

        for m in cls["methods"]:
            if m["lines"] > _LIMITS["function"]:
                over = (m["lines"] - _LIMITS["function"]) / _LIMITS["function"] * 100
                violations.append(
                    (
                        over,
                        f"{path}:{m['lineno']}: Method too large: {cls['name']}:{m['name']} - {m['lines']} lines ({over:.1f}% over)",
                    )
                )

    # Top-level functions
    for fn in report["functions"]:
        if fn["lines"] > _LIMITS["function"]:
            over = (fn["lines"] - _LIMITS["function"]) / _LIMITS["function"] * 100
            violations.append(
                (
                    over,
                    f"{path}:{fn['lineno']}: Function too large: {fn['module']}:{fn['name']} - {fn['lines']} lines ({over:.1f}% over)",
                )
            )

    return violations


def main():
    # Pre-commit passes filenames as arguments
    cli_files = [Path(f) for f in sys.argv[1:] if f.endswith(".py")]

    if cli_files:
        files = [f for f in cli_files if f.exists()]
        print("Checking Python files from pre-commit\n")
    else:
        # Manual fallback
        files = get_staged_python_files()
        print("Checking STAGED Python files (no filenames passed)\n")

    if not files:
        return 0

    violations = []

    for path in files:
        report = analyze_file(path)
        violations.extend(check_size_report(path, report))

    if violations:
        print("\nSize violations (sorted by % over):\n")
        for over, msg in sorted(violations, key=lambda x: x[0], reverse=True):
            print(msg)

        print("\nCommit blocked due to size violations.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
