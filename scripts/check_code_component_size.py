import ast
import os
import subprocess
import sys
from pathlib import Path

_LIMITS = {
    "module": 1000,
    "function": 60,
    "classes": 800,
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
                "lines": node.end_lineno - node.lineno + 1,
                "methods": [],
            }

            for item in node.body:
                if isinstance(item, ast.FunctionDef):
                    class_info["methods"].append(
                        {
                            "name": item.name,
                            "lines": item.end_lineno - item.lineno + 1,
                        }
                    )

            classes.append(class_info)

        elif isinstance(node, ast.FunctionDef):
            functions.append(
                {
                    "module": Path(path).stem,
                    "name": node.name,
                    "lines": node.end_lineno - node.lineno + 1,
                }
            )

    return {
        "module": path,
        "module_lines": module_lines,
        "classes": classes,
        "functions": functions,
    }


def get_staged_python_files():
    """Return staged .py files."""
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
        capture_output=True,
        text=True,
    )
    files = [Path(f) for f in result.stdout.splitlines() if f.endswith(".py")]
    return [f for f in files if f.exists()]


def get_all_python_files():
    return list(Path("./src/equinox").rglob("*.py"))


def main():
    # Priority: CLI flag > environment variable > default (staged only)
    mode = "staged"

    if "--all" in sys.argv:
        mode = "all"
    elif "--staged" in sys.argv:
        mode = "staged"
    else:
        env_mode = os.getenv("SIZE_CHECK_MODE")
        if env_mode in ("all", "staged"):
            mode = env_mode

    if mode == "all":
        files = get_all_python_files()
        print("Checking ALL Python files\n")
    else:
        files = get_staged_python_files()
        print("Checking STAGED Python files\n")

    if not files:
        return 0

    violations = False

    for path in files:
        report = analyze_file(path)

        if report["module_lines"] > _LIMITS["module"]:
            print(f"\nModule too large: {path} - {report['module_lines']} lines")
            violations = True

        for cls in report["classes"]:
            if cls["lines"] > _LIMITS["classes"]:
                print(f"Class too large: {cls['name']} - {cls['lines']} lines")
                violations = True

            for m in cls["methods"]:
                if m["lines"] > _LIMITS["function"]:
                    print(f"Method too large: {cls['name']}:{m['name']} - {m['lines']} lines")
                    violations = True

        for fn in report["functions"]:
            if fn["lines"] > _LIMITS["function"]:
                print(f"Function too large: {fn['module']}:{fn['name']} - {fn['lines']} lines")
                violations = True

    if violations:
        print("\nCommit blocked due to size violations.")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
