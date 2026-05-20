## Description

<!-- Brief summary of the change -->

## Type of Change

- [ ] Bug fix (non-breaking change that fixes an issue)
- [ ] New feature (non-breaking change that adds functionality)
- [ ] Breaking change (fix or feature that causes existing functionality to change)
- [ ] Documentation update
- [ ] Security hardening
- [ ] Performance optimization
- [ ] Refactoring
- [ ] Schema migration (new `Migration` entry in `migrations.py`)

## Related Issues

Closes #(issue number, if applicable)

## Motivation and Context

<!-- Explain why this change is needed -->

## Testing

<!-- Describe how you tested your changes -->
- [ ] Unit tests added/updated
- [ ] Integration tests added/updated
- [ ] Manual testing completed

## Checklist

### Code Quality

- [ ] Code follows project style guide (`pre-commit run --all-files` passes locally)
- [ ] Functions ≤ 30 lines, classes ≤ 400 lines, modules ≤ 1000 lines
- [ ] Comments added for complex logic; docstrings updated for public APIs
- [ ] No unnecessary complexity introduced

### Architecture Boundaries

- [ ] Business logic lives in `application/` or `core/` - not inside GUI modules
- [ ] GUI modules are presentation-only (no direct storage manager construction in request/history/collection flows)
- [ ] User-visible error dialogs use `gui/error_presenter.py`, not ad-hoc `QMessageBox`
- [ ] New URL/query manipulation uses `core/urls/` package helpers
- [ ] New request orchestration uses `core/client/` pipeline (not direct httpx calls)

### Security & Validation

- [ ] All user inputs validated via `Validator` façade (`core/validation/__init__.py`)
- [ ] No hardcoded secrets or credentials
- [ ] No SQL string formatting - parameterized queries only
- [ ] Auth data encrypted via `_serialize_auth` / `_deserialize_auth` boundary (`storage/collections/auth.py`)
- [ ] Security scan passes: `bandit -r src/equinox --severity-level=medium` (no new medium/high findings)

### Type Safety

- [ ] Explicit type hints on all new functions/methods
- [ ] `mypy --strict src tests` passes locally
- [ ] No `# type: ignore` added without a justification comment
- [ ] `Optional[X]` used instead of bare `X | None` for Python 3.9 compat

### Testing

- [ ] Coverage ≥ 85% maintained (`pytest --cov-fail-under=85`)
- [ ] Tests pass on Python 3.9, 3.10, 3.11, 3.12
- [ ] No flaky tests introduced

### Schema Changes (if applicable)

- [ ] New `Migration` entry appended to `MIGRATIONS` list in `migrations.py` (never edit `schema.sql`)
- [ ] `CREATE TABLE IF NOT EXISTS` or `ALTER TABLE ADD COLUMN` used (idempotent)
- [ ] Migration test added to `tests/storage/test_migrations.py`
- [ ] `AGENTS.md` "Current schema version" section updated

### Documentation

- [ ] `README.md` updated for user-facing changes
- [ ] `AGENTS.md` updated if architecture, data flow, or key files changed
- [ ] Changelog entry added in `changelog/` (anything beyond a minor fix)
- [ ] Docstrings updated for changed public APIs

### Performance

- [ ] No unintended performance regressions
- [ ] Large operations use structured logging with timing info
- [ ] Benchmark baseline confirmed for history/search changes (if applicable)

## Reviewer Notes

<!-- Optional: context that helps reviewers understand the change -->

## Screenshots (if applicable)

<!-- For GUI changes, add before/after screenshots -->
