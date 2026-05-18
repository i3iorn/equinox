#!/usr/bin/env python
"""
CI Toolchain Verification Script

Ensures that all tools used in CI workflows are properly declared in dependency manifests.
Prevents silent CI failures due to missing tools.

Usage:
    python scripts/verify_ci_toolchain.py
    # Exit 0 if OK, 1 if mismatches found

Integration:
    Add to pre-commit hooks:
        - repo: local
          hooks:
            - id: verify-ci-toolchain
              name: Verify CI toolchain consistency
              entry: python scripts/verify_ci_toolchain.py
              language: python
              stages: [commit]
              files: '(\\.github/workflows|pyproject\\.toml|setup\\.py)'
"""

import re
import sys
from pathlib import Path
from typing import Dict, Set


def extract_ci_tools() -> Set[str]:
    """Extract tool names from CI workflow files."""
    ci_dir = Path(".github/workflows")
    if not ci_dir.exists():
        print("⚠️  No .github/workflows directory found")
        return set()

    tools = set()

    for workflow_file in ci_dir.glob("*.yml"):
        content = workflow_file.read_text()

        # Extract tool invocations (common patterns)
        # Patterns: "tool --version", "tool --check", "tool -r", etc.
        patterns = [
            r"^\s+- (black|isort|ruff|mypy|bandit|safety|pytest)(\s|$)",
            r"(black|isort|ruff|mypy|bandit|safety|pytest)\s+--",
            r"(python\s+)?(-m\s+)?(black|isort|ruff|mypy|bandit|safety|pytest)",
        ]

        for pattern in patterns:
            matches = re.findall(pattern, content, re.MULTILINE)
            for match in matches:
                if isinstance(match, tuple):
                    # Extract tool name from tuple (first non-empty group)
                    tool = next((m for m in match if m and m not in ("", "-m")), None)
                    if tool:
                        tools.add(tool)
                else:
                    tools.add(match)

    return tools


def extract_declared_deps() -> Dict[str, Set[str]]:
    """Extract declared dev dependencies from pyproject.toml and setup.py."""
    deps = {"pyproject.toml": set(), "setup.py": set()}

    # Parse pyproject.toml
    if Path("pyproject.toml").exists():
        pyproject_content = Path("pyproject.toml").read_text()
        # Find [project.optional-dependencies] dev section
        match = re.search(
            r'\[project\.optional-dependencies].*?dev\s*=\s*\[(.*?)]',
            pyproject_content,
            re.DOTALL,
        )
        if match:
            dev_section = match.group(1)
            # Extract package names
            packages = re.findall(r'"([\w\-]+)', dev_section)
            deps["pyproject.toml"].update(packages)

    # Parse setup.py
    if Path("setup.py").exists():
        setup_content = Path("setup.py").read_text()
        # Find extras_require["dev"] section
        match = re.search(
            r'extras_require\s*=\s*\{.*?"dev":\s*\[(.*?)]',
            setup_content,
            re.DOTALL,
        )
        if match:
            dev_section = match.group(1)
            # Extract package names
            packages = re.findall(r'"([\w\-]+)', dev_section)
            deps["setup.py"].update(packages)

    return deps


def main() -> int:
    """Verify CI toolchain consistency."""
    print("🔍 Verifying CI toolchain consistency...\n")

    # Step 1: Extract tools used in CI
    ci_tools = extract_ci_tools()
    if not ci_tools:
        print("⚠️  No tools found in CI workflows (might need manual review)")
        return 0

    print(f"✓ Found {len(ci_tools)} tools in CI workflows:")
    for tool in sorted(ci_tools):
        print(f"  - {tool}")

    # Step 2: Extract declared dependencies
    deps = extract_declared_deps()

    print("\n✓ Declared dev dependencies:")
    for source, packages in deps.items():
        print(f"  {source}: {len(packages)} packages")
        for pkg in sorted(packages)[:5]:  # Show first 5
            print(f"    - {pkg}")
        if len(packages) > 5:
            print(f"    ... and {len(packages) - 5} more")

    # Step 3: Check for mismatches
    print("\n🔍 Checking for mismatches...")

    all_declared = set()
    for packages in deps.values():
        all_declared.update(packages)

    # Normalize tool names (e.g., "pytest" -> "pytest", "mypy" -> "mypy")
    tool_name_map = {
        "black": "black",
        "isort": "isort",
        "ruff": "ruff",
        "mypy": "mypy",
        "bandit": "bandit",
        "safety": "safety",
        "pytest": "pytest",
    }

    missing_deps = set()
    for tool in ci_tools:
        normalized = tool_name_map.get(tool, tool)
        if normalized not in all_declared:
            missing_deps.add(tool)

    if missing_deps:
        print(f"\n❌ MISMATCH FOUND: The following tools are used in CI but not declared:")
        for tool in sorted(missing_deps):
            print(f"  - {tool}")
        print(
            "\n📝 Fix: Add these tools to [project.optional-dependencies] dev in pyproject.toml"
        )
        return 1

    # Step 4: Check for consistency between pyproject.toml and setup.py
    print("\n🔍 Checking pyproject.toml vs setup.py consistency...")
    if not deps["setup.py"]:
        print("✅ setup.py is acting as a compatibility shim (no duplicated dependency declarations).")
    elif deps["pyproject.toml"] and deps["setup.py"]:
        if deps["pyproject.toml"] != deps["setup.py"]:
            pyproject_only = deps["pyproject.toml"] - deps["setup.py"]
            setup_only = deps["setup.py"] - deps["pyproject.toml"]

            if pyproject_only:
                print(f"\n⚠️  In pyproject.toml but not setup.py:")
                for pkg in sorted(pyproject_only):
                    print(f"  - {pkg}")

            if setup_only:
                print(f"\n⚠️  In setup.py but not pyproject.toml:")
                for pkg in sorted(setup_only):
                    print(f"  - {pkg}")

            print(
                "\n💡 Note: pyproject.toml is authoritative. setup.py should mirror it."
            )
            return 1

    print("\n✅ All tools are properly declared in dependencies!")
    if deps["setup.py"]:
        print("✅ pyproject.toml and setup.py are in sync!")
    return 0


if __name__ == "__main__":
    sys.exit(main())

