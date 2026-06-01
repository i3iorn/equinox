import ast
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_LIMITS = {
    "module": 1000,
    "function": 60,
    "class": 500,
}

# ------------------------------------------------------------
# Data structures
# ------------------------------------------------------------


@dataclass
class FunctionInfo:
    name: str
    lineno: int
    lines: int
    parent: str | None = None  # class name or None


@dataclass
class ClassInfo:
    name: str
    lineno: int
    lines: int
    methods: list[FunctionInfo]


@dataclass
class FileReport:
    path: Path
    module_lines: int
    classes: list[ClassInfo]
    functions: list[FunctionInfo]


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------


def safe_len(node):
    """Return number of lines for an AST node, safely."""
    if hasattr(node, "end_lineno") and node.end_lineno:
        return node.end_lineno - node.lineno + 1
    return 0


def analyze_file(path: Path) -> FileReport:
    source = path.read_text(encoding="utf-8-sig")
    tree = ast.parse(source)
    module_lines = len(source.splitlines())

    classes = []
    functions = []

    # Skip tests
    if "test" in path.as_posix():
        return FileReport(path, module_lines, classes, functions)

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            cls = ClassInfo(
                name=node.name,
                lineno=node.lineno,
                lines=safe_len(node),
                methods=[],
            )
            classes.append(cls)

        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            parent = getattr(node, "parent_class", None)
            fn = FunctionInfo(
                name=node.name,
                lineno=node.lineno,
                lines=safe_len(node),
                parent=parent,
            )
            functions.append(fn)

    # Attach methods to classes
    class_map = {c.name: c for c in classes}
    for fn in functions:
        if fn.parent in class_map:
            class_map[fn.parent].methods.append(fn)

    # Keep only top-level functions
    top_level_functions = [f for f in functions if f.parent is None]

    return FileReport(path, module_lines, classes, top_level_functions)


def annotate_parents(tree):
    """Annotate function nodes with parent class names."""
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            if isinstance(node, ast.ClassDef) and isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef),
            ):
                child.parent_class = node.name
            annotate_parents(child)


def get_staged_python_files():
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
        capture_output=True,
        text=True,
    )
    return [Path(f) for f in result.stdout.splitlines() if f.endswith(".py") and Path(f).exists()]


def get_all_python_files():
    return list(Path("./src/equinox").rglob("*.py"))


# ------------------------------------------------------------
# Main
# ------------------------------------------------------------


def main():
    # Extract filenames passed by pre-commit (positional args)
    cli_files = [Path(a) for a in sys.argv[1:] if not a.startswith("-")]

    # Priority: explicit filenames > CLI mode flags > env > default
    mode = os.getenv("SIZE_CHECK_MODE", "staged")

    if "--all" in sys.argv:
        mode = "all"
    elif "--staged" in sys.argv:
        mode = "staged"

    # Parse --limit=N
    limit = None
    for arg in sys.argv:
        if arg.startswith("--limit="):
            try:
                limit = int(arg.split("=", 1)[1])
            except ValueError:
                print("Invalid --limit value; must be an integer.")
                return 1

    if limit is None:
        env_limit = os.getenv("SIZE_CHECK_LIMIT")
        if env_limit and env_limit.isdigit():
            limit = int(env_limit)

    # Determine file list
    if cli_files:
        # Pre-commit passes explicit filenames
        files = [f for f in cli_files if f.suffix == ".py" and f.exists()]
        print("Checking pre-commit provided files\n")
    elif mode == "all":
        files = get_all_python_files()
        print("Checking ALL Python files\n")
    else:
        files = get_staged_python_files()
        print("Checking STAGED Python files\n")

    if not files:
        return 0

    # Analyze files
    violations = []
    for path in files:
        report = analyze_file(path)

        # Module-level
        if report.module_lines > DEFAULT_LIMITS["module"]:
            over = (report.module_lines - DEFAULT_LIMITS["module"]) / DEFAULT_LIMITS["module"] * 100
            violations.append(
                (
                    over,
                    f"{path}:1: Module too large - {report.module_lines} lines ({over:.1f}% over)",
                ),
            )

        # Classes + methods
        for cls in report.classes:
            if cls.lines > DEFAULT_LIMITS["class"]:
                over = (cls.lines - DEFAULT_LIMITS["class"]) / DEFAULT_LIMITS["class"] * 100
                violations.append(
                    (
                        over,
                        f"{path}:{cls.lineno}: Class too large: {cls.name} - {cls.lines} lines ({over:.1f}% over)",
                    ),
                )

            for m in cls.methods:
                if m.lines > DEFAULT_LIMITS["function"]:
                    over = (m.lines - DEFAULT_LIMITS["function"]) / DEFAULT_LIMITS["function"] * 100
                    violations.append(
                        (
                            over,
                            f"{path}:{m.lineno} Method too large: {cls.name}:{m.name} - {m.lines} lines ({over:.1f}% over)",
                        ),
                    )

        # Top-level functions
        for fn in report.functions:
            if fn.lines > DEFAULT_LIMITS["function"]:
                over = (fn.lines - DEFAULT_LIMITS["function"]) / DEFAULT_LIMITS["function"] * 100
                violations.append(
                    (
                        over,
                        f"{path}:{fn.lineno} Function too large: {fn.parent}:{fn.name} - {fn.lines} lines ({over:.1f}% over)",
                    ),
                )

    # Sort and apply limit
    if violations:
        print("\nSize violations (sorted by % over):\n")

        sorted_violations = sorted(violations, key=lambda x: x[0], reverse=True)

        if limit is not None and limit > 0:
            sorted_violations = sorted_violations[:limit]

        for _, msg in sorted_violations:
            print(msg)

        if limit is not None and 0 < limit < len(violations):
            print(f"\n(Showing top {limit} of {len(violations)} violations)")

        print("\nCommit blocked due to size violations.")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
