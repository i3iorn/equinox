# Contributing to Equinox

Thank you for considering contributions to Equinox! This document provides guidelines and practical steps for developers.

## Table of Contents

1. [Code of Conduct](#code-of-conduct)
2. [Getting Started](#getting-started)
3. [Development Workflow](#development-workflow)
4. [Architecture Principles](#architecture-principles)
5. [Testing Requirements](#testing-requirements)
6. [Commit & PR Guidelines](#commit--pr-guidelines)
7. [Documentation Standards](#documentation-standards)

---

## Code of Conduct

Be respectful and inclusive. Report issues via GitHub security advisories.

---

## Getting Started

### Prerequisites

- Python 3.10+
- Git
- Virtual environment tool (venv, poetry, or conda)

### Local Setup

```bash
# Clone repository
git clone https://github.com/i3iorn/equinox.git
cd equinox

# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install with dev dependencies
pip install -e ".[dev]"

# Install pre-commit hooks (enforces local quality gate)
pre-commit install
```

---

## Development Workflow

### 1. Create a Feature Branch

```bash
git checkout -b feature/your-feature-name
# or
git checkout -b fix/your-bug-fix
```

Branch naming conventions:
- `feature/` — New functionality
- `fix/` — Bug fixes
- `refactor/` — Code cleanup without behavior change
- `docs/` — Documentation updates
- `test/` — Test improvements

### 2. Make Changes

Follow the architecture principles below. Keep commits atomic and descriptive.

### 3. Run Local Quality Gate

Before pushing, run the complete local quality check:

```bash
# Pre-commit hooks (automated formatting, linting, type checking)
pre-commit run --all-files

# Full test suite with coverage
pytest --cov=equinox --cov-report=html

# Security scans (blocking)
bandit -r src/equinox --severity-level=medium -s "B102,B113,B318,B608"
python scripts/check_dependency_vulnerabilities.py
```

Or run individual checks:

```bash
ruff check .              # Linting
ruff format --check .     # Format check
mypy src tests            # Type checking
pytest                    # Tests (fast run)
pytest -v --cov=equinox  # Tests with coverage (slower)
python scripts/check_dependency_vulnerabilities.py  # Dependency CVE gate
```

### 4. Commit and Push

```bash
git add .
git commit -m "feat: add new feature" -m "Longer description if needed"
git push origin feature/your-feature-name
```

Commit message style: conventional commits (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`).

### 5. Open a Pull Request

On GitHub:
- Use a descriptive title
- Fill in the PR template (if available)
- Link related issues
- Ensure CI passes

### 6. Code Review & Merge

After approval and CI passing:
- Squash-and-merge is preferred to keep history clean
- Or use regular merge if the commit history is clean

---

## Architecture Principles

### Service Boundaries

**Enforce strict separation of concerns:**

- **Application services** (`src/equinox/application/`) own business orchestration and domain logic.
- **GUI modules** (`src/equinox/gui/`) handle presentation only (events, rendering, dialogs).
- **Storage layer** (`src/equinox/storage/`) owns persistence and migrations.
- **Core utilities** (`src/equinox/core/`) provide reusable primitives (HTTP, validation, logging).

**Key rule:** GUI code must not construct storage managers directly or duplicate business logic. Use facades/services.

### Clean Code Standards

All code must follow:

- **SOLID principles** and separation of concerns
- **Single responsibility:** functions ≤ 30 lines, classes ≤ 400 lines (soft), 120/800 lines (hard limit)
- **DRY:** avoid duplication, but prioritize readability
- **Meaningful names:** variable, function, and class names should be self-explanatory
- **Explicit typing:** use type hints (no `Any` where specific types are known)
- **No global state:** avoid module-level mutable objects

### Security Standards

All changes involving user input or data handling must:

- **Validate input:** use `Validator` facade from `equinox.core.validation`
- **Fail securely:** no silent failures, explicit error handling
- **Never log secrets:** always redact sensitive data
- **Use safe defaults:** deny-by-default for permissions/features
- **Include tests:** security regressions must be caught early

### Module Size Limits

- **Modules:** ≤ 1000 lines (split into focused packages if larger)
- **Functions:** ≤ 30 lines (soft), ≤ 120 lines (hard limit)
- **Classes:** ≤ 400 lines (soft), ≤ 800 lines (hard limit)
- **Packages:** ≤ 15 direct child modules (consider reorganization if larger)

---

## Testing Requirements

### Test Coverage

- Minimum **87% coverage** (enforced by CI)
- New features must have corresponding tests
- Security/validation changes must have focused tests

### Running Tests

```bash
# Fast run (no coverage)
pytest --no-cov

# With coverage
pytest --cov=equinox --cov-report=html
open htmlcov/index.html  # View report

# Specific test file or function
pytest tests/core/test_validation.py::test_validator_rejects_sql_injection
pytest tests/gui/ -k "request_panel"  # Keyword filter

# Verbose output
pytest -v tests/core/
```

### Test Organization

Tests mirror the source tree:

```
tests/
  application/          # Application service tests
  auth/                 # Auth strategy tests
  core/                 # Core utility tests
  gui/                  # GUI component tests
  importers/            # Importer tests
  plugins/              # Plugin system tests
  security/             # Security tests
  storage/              # Storage and migration tests
```

### Security Test Examples

```python
# Always include security tests for new input handling:
def test_validator_rejects_malicious_input() -> None:
    with pytest.raises(ValidationError):
        Validator.validate_url("javascript:alert('xss')")

def test_auth_credentials_not_logged(caplog) -> None:
    # Ensure secrets are redacted in logs
    assert "secret-key" not in caplog.text
```

---

## Commit & PR Guidelines

### Commit Messages

Use conventional commits:

```
feat: add OAuth2 token refresh
fix: resolve request timeout on slow networks
docs: clarify plugin trust model
refactor: simplify auth resolution logic
test: add regression tests for capture engine
chore: bump dependency versions
```

**Format:**
```
<type>(<scope>): <subject>

<body (optional, explain why, not what)>

<footer (optional, link issues)>
Fixes #123
```

### PR Template

When opening a PR, include:

1. **What** — Brief description of the change
2. **Why** — Motivation or problem being solved
3. **Testing** — How you tested it; link to relevant tests
4. **Checklist:**
   - [ ] Tests pass locally (`pytest --cov=equinox`)
   - [ ] Pre-commit passes (`pre-commit run --all-files`)
   - [ ] Type hints are correct (`mypy src tests`)
   - [ ] Security implications considered (secrets not logged, inputs validated)
   - [ ] Dependency vulnerabilities pass (`python scripts/check_dependency_vulnerabilities.py`)
   - [ ] Changelog/docs updated (if user-facing)
   - [ ] No breaking changes (or explicitly noted)

### Code Review Expectations

- Be respectful and constructive
- Address all review comments or explain why they're not needed
- Tests must pass before merge
- At least one approval (owner or trusted maintainer)

---

## Documentation Standards

### Code Documentation

- **Docstrings:** all public functions/classes use Google-style docstrings
- **Inline comments:** explain *why*, not *what* (code should be self-explanatory)
- **Type hints:** use throughout; no bare `Any`
- **README.md:** update if user-facing behavior changes
- **AGENTS.md:** update if internal architecture changes

### Example Docstring

```python
def validate_request(request: Request, policy: str = "balanced") -> List[ValidationIssue]:
    """Validate request against policy profile.

    Args:
        request: Request object to validate.
        policy: Policy profile ('strict', 'balanced', 'permissive'). Defaults to 'balanced'.

    Returns:
        List of validation issues found (empty if valid).

    Raises:
        ValueError: If policy is unknown.
    """
```

### Changelog

If your change is user-facing:

1. Update or create a changelog file in `changelog/CHANGELOG_v*.md`
2. Include your change in the relevant section (Features, Fixes, etc.)
3. Reference related issue/PR numbers

---

## Common Development Tasks

### Adding a New Migration

```bash
# 1. Add migration to src/equinox/storage/migrations.py
Migration(
    version=25,
    description="Add retry_policy to requests",
    sql="ALTER TABLE requests ADD COLUMN retry_policy TEXT DEFAULT 'default';",
)

# 2. Add test in tests/storage/test_migrations.py
def test_migration_25_adds_retry_policy_column() -> None:
    # Verify column exists after migration
    ...

# 3. Run migrations
pytest tests/storage/test_migrations.py -v
```

### Adding a New CLI Command

```bash
# 1. Add command to src/equinox/cli/main.py
@click.command()
@click.option("--db-path", help="Database path")
def my_command(db_path: str) -> None:
    """Short description."""

# 2. Register in click group
@click.group()
def main_entry() -> None:
    pass

main_entry.add_command(my_command)

# 3. Test it
pytest tests/cli/test_my_command.py
equinox my-command --help
```

### Adding a New Feature to GUI

1. **Create a small service/facade** in `src/equinox/application/` if logic is involved
2. **Add GUI component** in `src/equinox/gui/` (panel, widget, or dialog)
3. **Inject dependencies** — don't construct storage managers in GUI
4. **Add tests** for both service and GUI logic
5. **Update docs** in `AGENTS.md` and `README.md` if it's a significant feature

---

## Troubleshooting

### Pre-commit Fails

```bash
# Update pre-commit hooks
pre-commit autoupdate

# Run specific hook manually (hook IDs from .pre-commit-config.yaml)
pre-commit run ruff --all-files
pre-commit run ruff-format --all-files
pre-commit run mypy-strict --all-files
```

### Type Hints Cause Errors

```bash
# Check specific file
mypy src/equinox/core/my_module.py

# Ignore known issues locally (as last resort)
# Add: # type: ignore [error-code]
```

### Tests Fail Locally but Pass on CI

```bash
# Ensure you're using the same Python version
python --version  # Should be 3.10+

# Run tests exactly as CI does
pytest --cov=equinox --cov-report=term -v

# Check if test isolation is broken
pytest tests/my_test.py::specific_test -v
```

---

## Asking for Help

- **Questions?** Open a discussion on GitHub
- **Found a bug?** Open an issue with reproduction steps
- **Security concern?** Use GitHub security advisories (private)
- **Architecture question?** Refer to `AGENTS.md` and the [Architecture Principles](#architecture-principles) section above

---

## Additional Resources

- **Architecture Guide:** See `AGENTS.md` for detailed component descriptions
- **Service Boundaries:** See [Architecture Principles](#architecture-principles) above for the GUI/application/storage separation
- **Security Policy:** See `docs/security_policy.md` for encryption/rotation practices
- **Development Workflow:** See `DEVELOPMENT.md` for detailed workflow steps

---

**Thank you for contributing to Equinox!**
