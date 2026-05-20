# Equinox

Equinox is a secure, local-first API testing tool with GUI and CLI workflows. Build, test, and monitor REST/HTTP APIs with zero data leaving your machine.

## Highlights

- **Local-first storage** (SQLite) with AES-256 encrypted credential/auth persistence
- **Rich request modeling** (collections, folders, environments, variables, global variables)
- **Built-in auth strategies** (OAuth2 with auto-refresh, Bearer, API Key, Basic, AWS SigV4)
- **Request/response features** (history, assertions, captures, pre/post scripts, code generation)
- **Smart UI features** (keyboard shortcuts, usage-based menu ranking, intelligent search)
- **Plugin-ready architecture** with security-focused validation defaults
- **Multi-format importers** (OpenAPI 3.x, Swagger 2.0, Postman, HAR, Insomnia)
- **Performance benchmarking** (built-in history search benchmark harness)

## Architecture Snapshot

Source is organized by cohesive packages aligned with functional domains:

```text
src/equinox/
  core/
    audit/            # Request/response audit logging
    auth_cipher.py    # Column-level Fernet encryption for credentials
    assertions.py     # Post-response test rules (JSON path, regex, status)
    captures.py       # Extract response values into variables
    client/           # HTTP client with retry, rate-limit, concurrency control
    codegen/          # Code generation (curl, Python, JavaScript, Go)
    config/           # Feature flags (EQUINOX_USE_OS_KEYRING, etc.)
    format/           # Error enrichment and transformation
    http/             # HTTP utilities (cookies, rate limiter, proxy helpers)
    interceptors/     # Pre/post-request hooks
    io/               # I/O parsing (cURL, .env, multipart)
    log_setup.py      # JSON structured logging to ~/.equinox/logs/equinox.log
    response_intelligence/  # Endpoint statistics, schema drift, security hints
    scripts/          # Sandboxed pre/post-request Python execution
    secret_managers/  # External secret backends (AWS Secrets, Vault, Bitwarden)
    urls/             # URL parsing, normalization, placeholder expansion
    validation/       # Zero-trust input validation (SQL, CRLF, SSRF patterns)
  gui/
    dialogs/          # Reusable dialog components
    request_panel/    # Request builder UI
    response_panel/   # Response viewer UI
    syntax_highlighter/  # JSON, Python, XML, YAML highlighting
    theme/            # Color schemes and fonts
    intelligence_panel.py   # Endpoint intelligence and suggestions
    intelligence_worker.py  # Background intelligence analysis
    window.py         # Main window and panel wiring
  storage/
    collections/      # Collection/request/folder CRUD
    history/          # Request/response history with search and indexing
    ui_usage_tracker.py  # UI interaction analytics (local only)
    database.py       # SQLite ORM with migrations
    migrations.py     # Versioned schema (currently v22)
  auth/               # Auth strategies (OAuth2, Bearer, Basic, AWS SigV4)
  importers/          # Multi-format importers (OpenAPI, Postman, HAR, Insomnia)
  exporters/          # Export to Postman, OpenAPI, HAR, cURL
  intelligence/       # Request recommender (header/param suggestions)
  cli/                # Click-based CLI (rotate-secrets command)
  plugins/            # Plugin system for extending functionality
  security/           # Redaction, secure storage, keystore
```

**Key Organizational Notes:**
- Core modules are organized into focused packages (`core/audit/`, `core/format/`, `core/io/`, `core/urls/`, `core/validation/`)
- Each package has clear domain responsibility with minimal cross-dependencies
- GUI is split into panels with shared widgets and dialogs
- Storage layer handles both collections and history with separate managers
- Security concerns centralized in `security/` and `core/auth_cipher.py`

## Requirements

- Python 3.9+

## Installation

```bash
git clone https://github.com/i3iorn/equinox.git
cd equinox
pip install -e .
```

For development:

```bash
pip install -e ".[dev]"
pre-commit install
```

## Quick Start

### GUI (PyQt6)

```bash
python -m equinox.gui.app
```

**Keyboard Shortcuts (v0.4.3+):**
- `Ctrl+1`–`Ctrl+8`: Navigate between sidebar tabs
- `Ctrl+R`: Send request (with focus on request panel)
- `Ctrl+/`: Open command palette

### CLI

```bash
# View help
equinox --help

# Rotate stored plaintext secrets to encrypted format
equinox rotate-secrets --db-path ./equinox.db

# Run performance benchmark
python scripts/benchmark_history_search.py --entries 5000 --runs 20
```

## Development Workflow

Run the local quality gate before opening a PR (matches `pyproject.toml` configuration):

```bash
pre-commit run --all-files
ruff check .
ruff format --check .
mypy src tests
pytest
bandit -r src/equinox
safety check
```

**Key development commands:**

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run full test suite with coverage
pytest --cov=equinox --cov-report=html

# Run specific test categories
pytest tests/core/                      # Core utilities
pytest tests/storage/                   # Database and migrations
pytest tests/gui/                       # GUI components
pytest tests/auth/                      # Auth strategies
pytest tests/importers/                 # Importers
pytest tests/security/                  # Security

# Generate HTML coverage report
pytest --cov=equinox --cov-report=html
open htmlcov/index.html

# Check type hints
mypy src tests

# Run security scan
bandit -r src/equinox
```

## Performance & Observability

### Performance Benchmark Harness

Use the built-in history-search benchmark to catch regressions in search/index behavior and establish baselines.

```bash
python scripts/benchmark_history_search.py --entries 5000 --runs 20
```

Output includes JSON metrics suitable for CI trend tracking:

```json
{
  "entries": 5000,
  "runs": 20,
  "metrics": {
    "min_ms": 12.5,
    "avg_ms": 45.3,
    "p95_ms": 67.2,
    "max_ms": 89.1
  }
}
```

**Integration:** Add to CI pipeline to detect performance regressions automatically.

### Structured Logging

Equinox uses structured JSON logging for observability:

- **Location:** `~/.equinox/logs/equinox.log`
- **Format:** JSON with timestamp, level, operation, correlation ID
- **Sensitive Data:** Never logged (automatic redaction of secrets, tokens, PII)
- **Levels:** DEBUG, INFO, WARNING, ERROR, CRITICAL

### UI Usage Analytics

Starting in v0.4.3, Equinox tracks local usage patterns to intelligently rank menu items and improve discoverability:

- **Privacy:** All data stored locally; never transmitted
- **Control:** Disable with `EQUINOX_TRACK_UI_USAGE=0`
- **View Stats:** Settings → Usage Statistics
- **Tracked:** Command palette usage, menu items, tab navigation, environment switching

## Architecture Decisions

Refer to `docs/adr/ADR-0001-security-boundaries-and-extension-safety.md` for architectural decision rationale.

## Plugin Trust Model

> [!WARNING]
> Plugins run as trusted local, in-process extensions with the same user-level access as Equinox. Permission checks, checksums, and allowlists are policy guardrails, not a hard isolation boundary.

## Safe Change Checklist

Before requesting review, confirm all items below:

- [ ] **Validation boundary preserved:** All untrusted input paths go through the `Validator` facade (`from equinox.core.validation import Validator`)
- [ ] **Plugin permissions:** Least-privilege and explicit; no implicit capability expansion
- [ ] **Deny-by-default plugin policy:** Still holds for newly introduced plugin behavior
- [ ] **Audit logs:** Security-relevant actions emit audit logs with useful context and correlation IDs
- [ ] **Schema updates:** Database schema changes only in `storage/migrations.py` (append new `Migration` entries)
- [ ] **Migration testing:** Upgrades from previous versions covered by `tests/storage/test_migrations.py`
- [ ] **History capture limits:** Bounded capture and search limits (no unbounded body or regex work)
- [ ] **Security regression tests:** Updated with validation, redaction, permissions, migrations
- [ ] **Keyboard shortcuts:** Don't conflict with input handling or platform shortcuts
- [ ] **Performance:** No regression in critical paths (benchmark harness validates)
- [ ] **UI usage tracking:** If adding new UI actions, integrate with `UIUsageTracker`

## Architecture Decisions

- `docs/adr/ADR-0001-security-boundaries-and-extension-safety.md`

## Examples

See `examples/` for sample collections, environments, plugins, and scripts.

## Troubleshooting

### Database Lock on Startup

**Symptom:** `OperationalError: database is locked` on startup

**Solution:** Equinox automatically retries database connections with exponential backoff (0.1s, 0.2s, 0.4s). If this persists:
- Close any other Equinox instances
- Delete `.equinox/equinox.db-wal` and `.equinox/equinox.db-shm` (WAL files)
- Restart Equinox

**Prevention:** WAL mode improves concurrent reader/writer access but requires cleanup during extended inactivity.

### History Body Capture Toggle Leaks Across Tests

**Symptom:** Inconsistent test behavior; some tests don't capture history bodies

**Cause:** `set_capture_bodies(False)` in `core/history_config.py` updates process-global state. If a test disables it without resetting, later tests may unexpectedly store `None` for history bodies.

**Solution:** Always reset in teardown/finalizer:

```python
from equinox.core.history_config import set_capture_bodies

@pytest.fixture(autouse=True)
def reset_history_capture():
    yield
    set_capture_bodies(True)  # Reset after test
```

### Worker Threads Crash When Parent Widget Deleted

**Symptom:** Crashes when closing the intelligence panel while analysis is running

**Solution:** Fixed in v0.4.2. Update to latest version. Defensive parent widget checks prevent orphaned worker access.

### OAuth2 Token Request Fails with 401

**Symptom:** "Unauthorized" error when requesting OAuth2 token

**Solution (v0.4.2+):** Equinox now supports both standard client credentials (POST body) and HTTP Basic authentication. Verify:
- Client ID and secret are correct
- Token URL is reachable and returns 401 (not other errors)
- Server supports one of: POST body `client_id`/`client_secret`, or HTTP Basic auth

### Keyboard Shortcuts Don't Work in Text Fields

**Symptom:** `Ctrl+1` selects all text instead of switching tabs

**Solution:** Keyboard shortcuts only activate when focus is on the main window. Click a neutral area (tab bar, status bar) before using navigation shortcuts. This is intentional to prevent conflicts with text editing.

### UI Usage Tracking Uses Too Much Storage

**Symptom:** `ui_usage` table grows unexpectedly large

**Solution:** Clear tracking data via Settings → Usage Statistics → Clear All. Or disable tracking:
```bash
export EQUINOX_TRACK_UI_USAGE=0
python -m equinox.gui.app
```

## Contributing

Contributions are welcome. Please open an issue or submit a PR with a clear change description.

When adding code, keep service boundaries explicit:
- Put orchestration/business logic in application services (for request flows, prefer `src/equinox/application/requests/`).
- Keep GUI modules presentation-focused (event handling, rendering, and dialog wiring).
- Do not add direct storage-manager construction in GUI panels/mixins.
- Route user-visible failures through `src/equinox/gui/error_presenter.py`.

## Security

If you discover a vulnerability, open a private GitHub security advisory.

## License

MIT License. See `LICENSE`.
