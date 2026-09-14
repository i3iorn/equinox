# AGENTS.md - Equinox Developer Guide

This guide helps AI agents understand Equinox architecture and development practices to avoid common mistakes and ramp up quickly.

## Critical Commands

```bash
# Setup
pip install -e ".[dev]" && pre-commit install

# Before every commit
pre-commit run --all-files
pytest --no-cov             # Fast test run

# Before opening a PR
pytest --cov=equinox --cov-report=html  # Coverage gate (≥87% on CI)
python scripts/check_dependency_vulnerabilities.py  # Blocking CVE gate

# Specific tests
pytest tests/storage/test_migrations.py  # Migration system
pytest tests/core/test_security_comprehensive.py  # Security tests
pytest tests/core/test_auth_encryption.py  # Auth encryption boundary
pytest tests/storage/test_request_persistence.py  # Autosave & path_params

# Lint / typecheck
ruff check .
ruff format --check .
mypy src tests  # Must pass strict (matches CI)
bandit -r src/equinox --severity-level=medium -s "B102,B113,B318,B608"
```

**Note:** CI runs `mypy --strict src`, not `mypy src tests`. Local mypy must match CI exactly — `mypy src tests` is the documented command.

## Architecture (Key Boundaries)

- **GUI** (`gui/`): Presentation only — event handlers, rendering, dialog wiring. Must NOT construct storage managers directly.
- **Application** (`application/`): Business logic and orchestration — request flows, collections, history.
- **Core** (`core/`): Reusable primitives (HTTP, validation, IO, URLs).
- **Storage** (`storage/`): Persistence and versioned migrations. `storage/history/` is a package with split responsibilities.
- **Entry points**: `gui/app.py` (GUI), `cli/main.py` (CLI), `Database.__init__` (auto-runs migrations).

## Database & Migrations — Do Not Skip

**Never modify `schema.sql` directly.** The authoritative schema is the cumulative result of all migrations in `MIGRATIONS`. schema.sql is reference only.

- Add new `Migration` entry to `storage/migrations.py` with next version number (currently **v25**).
- Use `CREATE TABLE IF NOT EXISTS` or `ALTER TABLE ADD COLUMN` (runner auto-skips if exists via PRAGMA table_info check).
- Write test in `tests/storage/test_migrations.py` that verifies column/table exists.
- Runner runs automatically on `Database.__init__` — no manual invocation needed.

**Critical gotcha**: `Database.lock` is a read-only `@property` — it has NO setter. Tests constructing bare `Database` via `Database.__new__` must set `db._lock` (private attribute), not `db.lock`.

## Security Patterns

1. **Zero-trust validation**: ALL untrusted inputs → `Validator` facade (`from equinox.core.validation import Validator`). Never validate inline.
2. **Auth encryption chain**: `core/auth_cipher.py` (facade) → `security/auth_cipher.py` (crypto) → `storage/auth_cipher_storage.py` (storage helpers). Values use `enc:` prefix with Fernet.
3. **Database**: Parameterized queries only — no string formatting in SQL. WAL mode for concurrent access.
4. **Plugins**: NOT sandboxes — trusted local in-process extensions with policy controls only.

## Test Gotchas

- **History body capture toggle leaks**: `set_capture_bodies(False)` in tests updates process-global state. MUST reset in teardown: `set_capture_bodies(True)`. Otherwise later tests store `None` for history bodies.
- Tests mirror source tree: `tests/storage/`, `tests/core/`, `tests/gui/`, `tests/auth/`, `tests/importers/`, `tests/security/`, `tests/flags/`.

## GUI Autosave & Dirty Flag Rules

- `_send_request()` must **never** call `_clear_dirty()`. Sending is not a save — dirty flag stays True so autosave writes to DB.
- `_clear_dirty()` is called at end of `load_request()` and after successful `_save_request()`.
- `autosave_current()` reads `self._auth` (own auth only) — never inherited `effective_auth`.
- `_save_request()` must update `self.current_request` with returned DB `id` and `collection_id` so subsequent autosaves target correctly.
- `path_params` JSON column must be kept in sync between save flows and storage hydration.

## Response Body Storage

`Response.body` is `bytes` (from httpx), but history stores as decoded text. When reconstructing from history, encode back:
```python
raw_body = entry.get("response_body") or ""
body_bytes = raw_body.encode("utf-8") if isinstance(raw_body, str) else (raw_body or b"")
```

## Keyboard Shortcuts (v0.4.3+)

- `Alt+1` through `Alt+6`: Sidebar tabs (Collections, History, Variables, Logs, Cookies, WebSocket)
- Request Builder and Response Viewer are always-visible main panels, not sidebar tabs
- Intelligence is a tab within Response Viewer, not left sidebar
- Shortcuts only activate when focus is on main window (intentional — won't work in text fields)

## Import Conventions

- `core/urls/`: Use `from equinox.core.urls import normalize_url` (package `__init__` re-exports all)
- `core/io/`: `parse_curl` via `from equinox.core.io import parse_curl` (preferred) or legacy `from equinox.core.curl_parser import parse_curl`
- Syntax highlighting: `gui/syntax_highlighter/` package; `PythonHighlighter` imported from its module directly
- `security/__init__.py`: Public security facade for cross-module imports (`redact_headers`, `redact_url`, `redact_body`, etc.)

## Multi-Server Import Behaviour

OpenAPI 3.x with N servers → N collections created (one per server), named `"<title> — <description>"` with `BASE_URL` collection variable each. Postman `{{baseUrl}}` tokens left unresolved for runtime environment resolution.

## Feature Flags

`EQUINOX_USE_OS_KEYRING`, `EQUINOX_HISTORY_CAPTURE_BODIES`, `EQUINOX_TRACK_UI_USAGE` in `core/config/flags.py`. Disable UI tracking with `EQUINOX_TRACK_UI_USAGE=0`.

## Worker Thread Safety

`gui/intelligence_worker.py` checks parent validity before work:
```python
if not self.parent or not self.parent.isVisible():
    logger.info("Parent widget no longer valid, cancelling")
    return
```

## Troubleshooting

**Database lock on startup**: Close other Equinox instances, delete `.equinox/equinox.db-wal` and `.equinox/equinox.db-shm`, restart. WAL mode requires cleanup during extended inactivity.

## Key Files Reference

| File | Purpose |
|------|---------|
| `storage/migrations.py` | Versioned schema — **only place to change DB schema** (v25) |
| `storage/database.py` | SQLite ORM; `.lock` is read-only property |
| `storage/ui_usage_tracker.py` | UI analytics and action ranking (v0.4.3+) |
| `core/validation/__init__.py` | `Validator` façade — always start here for new input types |
| `core/client/http_client.py` | HTTP client with retry, rate-limit, concurrency |
| `auth/oauth2.py` | OAuth2 with auto-refresh; tokens encrypted at DB layer |
| `gui/intelligence_worker.py` | Background intelligence with defensive parent checks |
| `gui/syntax_highlighter/` | Centralized highlighting package |

## References

- **DEVELOPMENT.md**: Step-by-step workflow and common tasks
- **CONTRIBUTING.md**: Code standards and PR process (note: coverage figure "87%" in docs is stale — pyproject.toml `fail_under = 74` is the actual threshold)
- **README.md**: Architecture snapshot, commands, troubleshooting
- This file (`AGENTS.md`): Deep-dive architecture details for AI agents
