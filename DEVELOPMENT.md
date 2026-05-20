# Development Workflow

Practical guide for day-to-day development on Equinox. For general contribution guidelines, see `CONTRIBUTING.md`.

## Quick Reference

### Setup

```bash
git clone https://github.com/i3iorn/equinox.git && cd equinox
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -e ".[dev]" && pre-commit install
```

### Before Every Commit

```bash
pre-commit run --all-files  # Format, lint, type-check, security scan
pytest --no-cov             # Run tests (fast)
```

### Before Opening a PR

```bash
pytest --cov=equinox --cov-report=html  # Full coverage check (slower)
# Check htmlcov/index.html for coverage gaps
```

---

## Detailed Workflow

### 1. Starting Work

```bash
# Update from main branch
git fetch origin
git checkout main
git pull origin main

# Create feature branch
git checkout -b feature/your-feature-name
```

**Branch naming:**
- `feature/auth-oauth2-device-flow` — New feature
- `fix/handle-redirect-timeout` — Bug fix
- `refactor/simplify-request-routing` — Code improvement (no behavior change)
- `docs/clarify-plugin-trust` — Documentation
- `test/add-validation-edge-cases` — Test improvements

### 2. Making Changes

**Follow the project structure:**

```
src/equinox/
├── application/          ← Business logic & orchestration
├── core/                 ← Reusable utilities (HTTP, validation, etc.)
├── gui/                  ← GUI components & panels (presentation only)
├── storage/              ← Persistence & migrations
├── auth/                 ← Auth strategies
└── ...
```

**Key principles:**

- **GUI is thin:** event handlers, rendering, dialog wiring
- **Logic in services:** `application/` facades handle orchestration
- **No GUI → Storage coupling:** use facades/services
- **Validate input:** always use `Validator` from `core.validation`
- **Security first:** no hardcoded secrets, redact logs, fail securely

**Example: Adding a new request feature**

```python
# 1. Create service/facade (if needed)
# src/equinox/application/requests/my_feature.py
def my_business_logic(request: Request) -> Result:
    """Pure business logic, no GUI imports."""
    ...

# 2. Add GUI handler
# src/equinox/gui/request_panel/mixins/my_mixin.py
class MyFeatureMixin:
    def on_button_click(self) -> None:
        # Call service, render result
        result = my_business_logic(self.current_request)
        self._display_result(result)

# 3. Add tests for both
# tests/application/requests/test_my_feature.py
# tests/gui/test_my_feature_mixin.py
```

### 3. Running Tests During Development

```bash
# Fast check (no coverage, fail-fast)
pytest --no-cov -x tests/

# Specific test file
pytest --no-cov tests/core/test_validation.py

# Specific test function
pytest --no-cov tests/core/test_validation.py::test_validator_rejects_sql

# Watch mode (requires pytest-watch, install with pip install pytest-watch)
ptw -- --no-cov

# With verbose output
pytest -vv tests/

# Showing print statements
pytest -vv -s tests/core/test_my_feature.py
```

### 4. Type Checking & Linting

```bash
# Type check everything
mypy src tests

# Type check specific module
mypy src/equinox/core/validation/

# Lint with ruff (shows issues)
ruff check src/

# Format code with ruff & black
ruff format src/ tests/
black src/ tests/

# Security scan
bandit -r src/equinox --severity-level=medium
```

### 5. Pre-commit Workflow

**Before pushing to GitHub:**

```bash
# Run all pre-commit hooks
pre-commit run --all-files

# If hooks modify files, review & re-stage
git add .
git commit -m "feat: my feature"

# Common issues & fixes:
# - Black format conflict? → Re-run black: black src/
# - Ruff wants changes? → Review with: ruff check src/ --diff
# - Mypy errors? → Fix types or add # type: ignore with reason
```

**Pre-commit does:**
- Format code (black, ruff)
- Check for merge conflicts
- Detect private keys & large files
- Type checking (mypy)
- Security scan (bandit)
- Custom checks (dependency consistency)

### 6. Committing Changes

```bash
# Review changes before staging
git diff src/

# Stage and commit
git add src/
git commit -m "feat: add new request feature"

# Or atomic commits for different concerns
git add src/equinox/application/requests/my_feature.py
git commit -m "feat: add my_feature service"

git add tests/application/requests/test_my_feature.py
git commit -m "test: add my_feature tests"

git add docs/
git commit -m "docs: document my_feature"
```

**Commit message conventions:**

```
feat: add new feature
feat(request_panel): support inline variables
fix: resolve timeout on slow networks
fix(validation): handle edge case in URL parsing
docs: update architecture guide
refactor: simplify auth resolution logic
test: add regression tests for X
chore: bump dependency versions
```

Use `<type>(<scope>): <description>`. Scope is optional but helpful.

### 7. Pushing & Opening PR

```bash
# Push to your fork
git push origin feature/your-feature-name

# On GitHub, open a PR with:
# - Clear title (matching commit style)
# - Description of what & why
# - Link to related issues
# - Checklist completion
```

**PR Checklist Template:**

```markdown
## What does this PR do?
Brief description of the change.

## Why?
Why is this change needed? What problem does it solve?

## Testing
How did you test this? Any manual steps?

## Checklist
- [ ] Tests pass: `pytest --cov=equinox`
- [ ] Pre-commit passes: `pre-commit run --all-files`
- [ ] No breaking changes (or noted)
- [ ] Documentation updated
- [ ] Security implications reviewed
```

### 8. After Review

**If changes requested:**

```bash
# Make changes
git add .
git commit -m "feedback: address review comments"

# Push again (force-push not needed if CI approved the branch)
git push origin feature/your-feature-name
```

**After approval:**

- Squash-and-merge preferred (keeps history clean)
- Or merge if commit history is clean & descriptive

```bash
# Locally, after merge:
git checkout main
git pull origin main
git branch -d feature/your-feature-name
```

---

## Common Workflows

### Adding a New Field to a Request

1. **Update the model:** `src/equinox/core/request.py`
2. **Add to GUI snapshot:** `src/equinox/gui/request_panel/panel.py` (snapshot builder)
3. **Create migration:** `src/equinox/storage/migrations.py`
4. **Update serialization:** if needed (history, persistence)
5. **Add tests:** model, GUI, migration, persistence
6. **Update docs:** `AGENTS.md` if it's significant

### Fixing a Bug

1. **Create failing test first:** reproduces the bug
2. **Implement fix:** make test pass
3. **Verify no regressions:** run full suite
4. **Commit:** `fix: describe the bug` + `Fixes #123`

### Refactoring a Module

1. **Ensure tests exist:** for current behavior
2. **Refactor incrementally:** small, reviewable changes
3. **Keep tests green:** don't move logic AND refactor simultaneously
4. **Commit:** `refactor: describe improvement` (no behavior change)

### Adding a New Test

```bash
# Create test file following the mirror pattern
# tests/core/test_my_component.py  mirrors  src/equinox/core/my_component.py

# Write test
pytest tests/core/test_my_component.py -v

# Ensure it passes
# Ensure coverage is captured: pytest --cov=equinox tests/core/test_my_component.py
```

### Updating Dependencies

```bash
# Check current state
pip list | grep equinox

# Bump version in pyproject.toml
# Run full test suite
pytest --cov=equinox

# Update lock files (if managed)
pip install pip-tools
pip-compile pyproject.toml

# Commit
git commit -m "chore: update dependencies"
```

---

## Debugging Tips

### Print Statements (during tests)

```python
# Add in test
print("DEBUG: my_var =", my_var)

# Run with output
pytest -vv -s tests/my_test.py
```

### Using Breakpoints (IDE)

```python
# Add in code
breakpoint()  # Python 3.7+

# Run tests without capture
pytest -vv --pdb tests/my_test.py
```

### Inspecting Test Fixtures

```python
@pytest.fixture
def my_fixture():
    obj = create_obj()
    print(f"Fixture: {obj.__dict__}")  # Debug during test setup
    yield obj

def test_something(my_fixture):
    print(f"Test: {my_fixture}")  # Print during test
```

### Database State in Tests

```python
# If tests involve database, check state:
def test_something(tmp_db_path):
    db = Database(tmp_db_path)
    
    # Do something
    ...
    
    # Inspect
    rows = db.fetchall("SELECT * FROM requests")
    print(f"Requests: {rows}")  # Debug output with pytest -s
```

---

## Continuous Integration

GitHub Actions runs when you push:

1. **Linting & Formatting:** ruff, black
2. **Type Checking:** mypy
3. **Security:** bandit
4. **Tests:** pytest with coverage (≥ 85% required)
5. **Code Coverage:** reported in PR

**Local CI simulation:**

```bash
# Do what CI does:
pre-commit run --all-files
pytest --cov=equinox --cov-report=term
bandit -r src/equinox --severity-level=medium
```

If CI fails and local passes:
- Check Python version (CI uses 3.9+)
- Check for test isolation issues
- Review CI logs for details

---

## Useful Commands Cheat Sheet

```bash
# Setup
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install

# Development
pytest --no-cov -x                      # Fast test run
pytest -vv -s tests/my_test.py          # Verbose with output
pytest --cov=equinox --cov-report=html # Full coverage
mypy src tests                          # Type check
ruff check src/                         # Lint
ruff format src/ tests/                 # Format
pre-commit run --all-files              # Full local CI

# Git
git checkout -b feature/my-feature
git add .
git commit -m "feat: description"
git push origin feature/my-feature

# Cleanup
git branch -D feature/my-feature        # Delete branch
git remote prune origin                 # Clean up remote tracking
```

---

## When Stuck

1. **Check existing issues** — someone may have faced it before
2. **Review similar code** — pattern-match against working examples
3. **Read `AGENTS.md`** — architecture details and conventions
4. **Ask in discussions** — GitHub discussions are for questions
5. **Open an issue** — if it's a real problem or missing documentation

---

**Happy coding!**

