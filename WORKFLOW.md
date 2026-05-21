# Equinox Project Workflow & Documentation Overview

This document provides a high-level overview of the Equinox project organization, documentation structure, and development workflow. It acts as the **entry point** for understanding how to navigate, contribute, and work on the project.

---

## Documentation Map

Your question likely falls into one of these categories. Find the right doc:

| **I want to...** | **Read this** | **What it covers** |
|---|---|---|
| Understand project architecture & design decisions | [AGENTS.md](AGENTS.md) | Deep-dive: components, data flow, security model, conventions, key files |
| Start contributing code | [CONTRIBUTING.md](CONTRIBUTING.md) | Code standards, clean code principles, testing requirements, commit guidelines |
| Work day-to-day on features | [DEVELOPMENT.md](DEVELOPMENT.md) | Step-by-step workflow, common tasks, debugging tips, git commands |
| Understand GUI service boundaries | [docs/gui_service_boundary_refactor_plan.md](docs/gui_service_boundary_refactor_plan.md) | Architecture refactoring phases (5-12), service boundary placement |
| Learn about security & plugin trust | [docs/security_policy.md](docs/security_policy.md) | Encryption, master passwords, secret rotation, plugin trust model |
| Get started quickly | [README.md](README.md) | Installation, quick start, highlights, troubleshooting |
| View release notes | [changelog/](changelog/) | Version history, changes per release |

---

## Project Organization

```
equinox/
├── src/equinox/           # Main source code
│   ├── application/       # Business logic & services (request, collections, history)
│   ├── core/              # Reusable utilities (HTTP, validation, encryption, etc.)
│   ├── gui/               # PyQt6 UI components (thin presentation layer)
│   ├── storage/           # Database layer with versioned migrations
│   ├── auth/              # Authentication strategies
│   ├── cli/               # Command-line interface
│   ├── plugins/           # Plugin system
│   └── security/          # Security utilities
├── tests/                 # Organized test suite (mirrors src/)
├── docs/                  # Architecture & policy docs
├── changelog/             # Release notes per version
├── CONTRIBUTING.md        # Contribution guidelines
├── DEVELOPMENT.md         # Day-to-day workflow guide
├── AGENTS.md              # Detailed architecture for AI/developers
├── README.md              # Project overview & quick start
└── pyproject.toml         # Python package metadata & config
```

---

## Key Architectural Principles

### The Service-Boundary Refactor (Phases 5-12 Complete)

Equinox follows a **strict service-boundary architecture** completed in v0.4.4–v0.4.5:

- **GUI is thin:** event handling, rendering, dialog wiring only
- **Application services** own business logic & orchestration (`application/requests/`, `application/collections/`, `application/history/`)
- **Storage layer** handles persistence (`storage/` with versioned migrations)
- **Core utilities** provide reusable primitives (`core/validation/`, `core/http/`, etc.)

**Rule:** GUI code must **never** construct storage managers directly. Use facades/services instead.

### Security: Fortress Mentality

- **Zero-trust validation:** all inputs validated before use
- **Auth encryption:** credentials encrypted at rest with Fernet (AES-256)
- **Fail securely:** no silent failures, explicit error handling
- **No hardcoded secrets:** never log or store credentials in plaintext
- **Plugin trust:** plugins are trusted in-process extensions (not sandboxed)

---

## Workflow at a Glance

### For New Contributors

```bash
# 1. Setup
git clone <repo>
cd equinox
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pre-commit install

# 2. Create feature branch
git checkout -b feature/my-feature

# 3. Make changes, test, commit
# (See DEVELOPMENT.md for detailed steps)

# 4. Before pushing
pre-commit run --all-files   # Format, lint, type-check
pytest --no-cov              # Run tests

# 5. Before opening PR
pytest --cov=equinox --cov-report=html  # Full coverage check

# 6. Push & open PR on GitHub
git push origin feature/my-feature
```

### For Code Review

**Reviewers** check:
- Tests pass (CI validates)
- Code follows architecture principles (thin GUI, services own logic)
- Security: secrets redacted, input validated, no hardcoded values
- Clean code: functions ≤ 30 lines, classes ≤ 400 lines
- Docs updated (AGENTS.md if architecture changed, README if user-facing)

---

## Testing Requirements

- **Minimum coverage:** 87% (enforced by CI)
- **Test location:** `tests/` mirrors `src/` structure
- **Run locally:** `pytest --cov=equinox --cov-report=html`
- **Security tests:** always test new validation/encryption code
- **Fast local gate:** `python scripts/run_affected_tests.py` (changed modules only)

---

## Code Standards at a Glance

| **Aspect** | **Standard** |
|---|---|
| **Functions** | ≤ 30 lines (soft), ≤ 60 lines (hard limit) |
| **Classes** | ≤ 400 lines (soft), ≤ 800 lines (hard limit) |
| **Modules** | ≤ 1000 lines (split if larger) |
| **Packages** | ≤ 15 direct children (reorganize if larger) |
| **Typing** | Explicit type hints, no bare `Any` |
| **Naming** | Self-explanatory variable/function/class names |
| **Security** | Always validate input, never hardcode secrets, redact logs |
| **GUI Rule** | No storage manager construction, use facades |

---

## Common Development Tasks

### Adding a New Request Feature

1. Create service/facade (if orchestration needed) in `application/requests/`
2. Add GUI handler in `gui/request_panel/`
3. Inject dependencies (don't construct storage managers)
4. Add tests for both service and GUI
5. Update `AGENTS.md` if architecture changed

See [DEVELOPMENT.md: Adding a new field to a request](DEVELOPMENT.md#adding-a-new-field-to-a-request) for detailed steps.

### Fixing a Bug

1. Create failing test first
2. Implement fix to pass test
3. Verify no regressions with full suite
4. Commit: `fix: describe the bug`

### Database Schema Change

1. Add migration to `storage/migrations.py`
2. Add test in `tests/storage/test_migrations.py`
3. Never modify `storage/schema.sql` directly

---

## Git Workflow

### Branch Naming

- `feature/auth-device-flow` — New feature
- `fix/handle-redirect-timeout` — Bug fix
- `refactor/simplify-request-routing` — Code improvement
- `docs/clarify-plugin-trust` — Documentation
- `test/add-validation-edge-cases` — Test improvements

### Commit Message Style

Use [conventional commits](https://www.conventionalcommits.org/):

```
feat: add new feature
fix(validation): handle edge case in URL parsing
docs: update architecture guide
refactor: simplify auth resolution logic
test: add regression tests for X
```

---

## CI/CD Pipeline

GitHub Actions runs on every push:

1. **Pre-commit hooks:** ruff format, type checking, security scan
2. **Tests:** full suite with ≥ 87% coverage requirement
3. **Security:** bandit checks for vulnerabilities
4. **Type checking:** mypy strict mode
5. **Dependency lock check:** `scripts/manage_requirements_lock.py --check` (validate-only; no CI writes)

**Simulate locally:**
```bash
pre-commit run --all-files
python scripts/run_affected_tests.py
python scripts/manage_requirements_lock.py --check
pytest --cov=equinox --cov-report=term
mypy src tests
bandit -r src/equinox --severity-level=medium
```

---

## Architecture Decision Records

Key architectural decisions are documented in:

- **Service boundaries:** [docs/gui_service_boundary_refactor_plan.md](docs/gui_service_boundary_refactor_plan.md)
- **Security model:** [docs/security_policy.md](docs/security_policy.md)
- **Plugin trust:** [AGENTS.md](AGENTS.md#plugin-trust-model-explicit)

---

## Getting Help

| **Question** | **Where to Look** |
|---|---|
| How do I set up the project? | [README.md](README.md#installation) → [DEVELOPMENT.md](DEVELOPMENT.md#setup) |
| What's the project architecture? | [AGENTS.md](AGENTS.md) |
| How do I write clean code? | [CONTRIBUTING.md](CONTRIBUTING.md#architecture-principles) |
| How do I add a new feature? | [DEVELOPMENT.md](DEVELOPMENT.md#common-workflows) |
| Where should new code go? | [CONTRIBUTING.md](CONTRIBUTING.md#architecture-principles) → [AGENTS.md](AGENTS.md#boundary-placement-rules-for-new-code) |
| How do I run tests? | [DEVELOPMENT.md](DEVELOPMENT.md#3-running-tests-during-development) |
| What's the plugin trust model? | [AGENTS.md](AGENTS.md#plugin-trust-model-explicit) |
| How do I debug? | [DEVELOPMENT.md](DEVELOPMENT.md#debugging-tips) |

---

## Quick Reference

### Before Every Commit

```bash
pre-commit run --all-files  # Format & lint
pytest --no-cov             # Fast tests
```

### Before Opening a PR

```bash
pytest --cov=equinox --cov-report=html  # Full coverage
mypy src tests                            # Type check
```

### Run the App

```bash
python -m equinox.gui.app
```

### Run Tests Locally

```bash
pytest              # Full suite
pytest -vv -s       # Verbose with output
pytest --no-cov     # Fast (no coverage)
pytest -k "my_test" # Specific test
```

---

## Document Ownership & Updates

| **Document** | **Maintained By** | **When to Update** |
|---|---|---|
| README.md | All contributors | User-facing changes, new features |
| CONTRIBUTING.md | Maintainers | Code standards change, new guidelines |
| DEVELOPMENT.md | Maintainers | Workflow changes, new tools |
| AGENTS.md | Maintainers | Architecture changes, new patterns |
| docs/ | Team | Architecture decisions, policies |
| changelog/ | Release manager | With each release |

---

## Version & Release Info

- **Current Version:** v0.4.8 (May 21, 2026)
- **Latest Changelog:** [changelog/CHANGELOG_v0.4.8.md](changelog/CHANGELOG_v0.4.8.md)
- **Release History:** [changelog/](changelog/)

---

## Summary

Equinox is organized around **strict service boundaries** with thin GUI, business logic in application services, and security-first architecture. Use this workflow guide to navigate the codebase, and refer to specific docs for detailed guidance on your task.

**Start here:**
1. Read the overview in `README.md`
2. Choose your task type and refer to the table above
3. Follow the relevant guide (DEVELOPMENT.md for most day-to-day work)
4. Check `AGENTS.md` for deep-dive architecture questions

**Happy contributing!**
