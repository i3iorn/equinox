# AGENTS.md - Equinox Developer Guide

This guide helps AI agents understand Equinox architecture and development practices.

## Architecture Overview

**Equinox** is a local-first API testing tool with GUI. Core data stored in SQLite with AES-256 encrypted secrets.

### Component Structure

- **`core/`**: HTTP client (package), request/response models (package), validation (package), audit (package), interceptors (package), assertions, captures, scripts (package), codegen (package), URL handling (package), error enrichment (package), response intelligence (package), secret managers (package), feature flags (`config/flags.py`)
  - **`core/util/`** — Utility constants and time helpers (`utc_now`, `to_iso_z`, size limit constants)
  - **`core/format/`** — Error enrichment and mapping (`enrich_exception`, `RichError`, `build_error_handlers`)
  - **`core/http/`** — HTTP protocol helpers (`CookieManager`, `RateLimiter`, `check_proxy_reachable`)
  - **`core/io/`** — I/O and parsing utilities (`parse_curl`, `parse_dotenv`, multipart handling)
  - **`core/urls/`** — URL handling package (`normalizer.py`, `parsing.py`, `utils.py`)
- **`gui/`**: PyQt6 panels — request builder (package), response viewer (package), collections (package), history, variables, logs, intelligence, websocket; shared widgets, dialogs (package), syntax highlighter (package), keyboard shortcuts, UI usage tracking
- **`storage/`**: SQLite layer — **versioned migrations** (`migrations.py`), collection/env/history (package) managers, variable groups, cookies, saved credentials, response intelligence
- **`auth/`**: Auth strategies — OAuth2 (auto-refresh + encrypted token storage), Bearer, API Key, Basic, AWS SigV4; factory module
- **`importers/`**: OpenAPI (multi-server, server-variable expansion), Postman (`{{baseUrl}}` resolution), HAR, Insomnia
- **`exporters/`**: Standalone package — Postman, OpenAPI, Insomnia, HAR, cURL exporters
- **`intelligence/`**: Request recommender (header/param suggestions from history)
- **`cli/`**: Click-based operational commands (currently `rotate-secrets`)
- **`security/`**: Shared security primitives (redaction, secure storage, keystore, secret rotation)
- **`plugins/`**: Plugin system for extending functionality

### Data Flow

```
User input → Validator (zero-trust, package) → Request model
  → Variable interpolation ({{VAR}} from .env / active DB environment)
  → InterceptorChain (pre-request hooks)
  → HTTPClient (rate-limiting, timeout, SSL, retries, concurrency)
  → httpx transport (HttpxDispatcher)
  → InterceptorChain (post-response hooks)
  → Assertions + Captures evaluated
  → Audit logger → Database (SQLite, versioned schema)
  → history_index updated (_HistoryIndexer)
  → GUI render
```

### Critical Entry Points

- **GUI**: `src/equinox/gui/app.py` — PyQt6 main entry; `window.py` wires panels together
- **CLI**: `src/equinox/cli/main.py` — Click command group (`rotate-secrets`)
- **Database**: `src/equinox/storage/database.py` — `Database.__init__` always calls `MigrationRunner.run()`
- **Migrations**: `src/equinox/storage/migrations.py` — add new `Migration` entries to `MIGRATIONS` list
- **HTTP**: `src/equinox/core/client/http_client.py` — `HTTPClient` runs `InterceptorChain` around every request
- **Importers**: `src/equinox/importers/openapi.py` — multi-server: one collection per server

## Schema Changes — Migration System

**Never modify `schema.sql` directly.** The `schema.sql` file is kept for reference only; the authoritative schema is the cumulative result of all migrations in `MIGRATIONS`.

### Adding a column or table

1. Append a new `Migration` entry to `MIGRATIONS` in `migrations.py` with the next version number.
2. Use `CREATE TABLE IF NOT EXISTS` or — for `ALTER TABLE ADD COLUMN` — the runner automatically skips the statement if the column already exists (`_execute_stmt` uses `PRAGMA table_info` check).
3. Write a test in `tests/storage/test_migrations.py` that verifies the column/table exists after migration.

```python
# Example — migrations.py
Migration(
    version=22,
    description="Add retry_count to requests",
    sql="ALTER TABLE requests ADD COLUMN retry_count INTEGER DEFAULT 0;",
),
```

The runner is triggered automatically on `Database.__init__` — no manual invocation needed.

### Current schema version

The schema is at **version 24** (migration 24 adds the `ui_usage` table for UI usage tracking). See `migrations.py` for the full list.

## Database — Key Implementation Notes

- `Database.lock` is a **read-only property** (`@property`) that returns `self._lock`. It has **no setter** — do not assign to `db.lock` directly. Tests that need to construct a bare `Database` instance (e.g. `Database.__new__`) must set `db._lock` (the private attribute) instead.
- `Database.__init__` stores the lock as `self._lock`; the public `.lock` property exposes it for callers that need to hold it across multiple operations (e.g. `MigrationRunner.run()`).

## Multi-Server Import Behaviour

### OpenAPI 3.x

`OpenAPIImporter.import_dict()` calls `_resolve_servers_openapi3()` which:
- Expands `{serverVariable}` templates using each variable's `default`
- Strips trailing `/` (preserving bare `/`)
- Falls back to `[ServerInfo(url="/")]` when the `servers` key is absent

**When a spec has N servers, N collections are created** — one per server — named `"<title> — <description>"`. Each collection also gets a `BASE_URL` collection variable.

Relative base URLs (e.g. `/`) are promoted to `https://{{BASE_URL}}` so requests pass URL validation.

### Postman collections

`_extract_collection_variables()` reads `collection.variable[]` and passes the resulting dict down to `_parse_request` and `_build_url`. `_resolve_postman_variable()` substitutes known `{{key}}` tokens; unknown tokens are left as-is so users can resolve them via an active environment at runtime.

## Security Architecture (Fortress Mentality)

**Zero-trust validation**: All user inputs validated before use, even from internal components.

### Plugin trust model (explicit)

- Plugins are trusted local, in-process extensions.
- Plugin permission checks, checksums, and allowlists are policy controls, not hard isolation.
- Do not describe plugin execution as a security sandbox boundary.

### Key Security Patterns

1. **Input Validation** (`core/validation/` package): SQL/command/CRLF/path-traversal patterns blocked; URL scheme/length enforced; header/body size limits. The public façade is `Validator` (imported from `core/validation/__init__.py`). Internal validators are in sub-modules (`_url.py`, `_headers.py`, `_body.py`, `_params.py`, `_path.py`, `_env.py`, `_ssrf.py`, `_guards.py`, `_limits.py`, `_patterns.py`). Always call `Validator.validate_*()` before processing input.
2. **Auth Encryption at Rest** (`core/auth_cipher.py`): All `auth_data` and `saved_credentials.config` columns are Fernet-encrypted (AES-256) at the `_serialize_auth`/`_deserialize_auth` boundary. Encrypted values carry an `enc:` prefix; legacy plaintext is read transparently (graceful migration). The key is stored at `~/.equinox/.key` (shared with `SecureStorage`).
3. **Database** (`storage/database.py`): Parameterized queries only — **no string formatting in SQL**. Thread-safe with `threading.Lock` (exposed as the `.lock` read-only property). WAL journal mode for concurrent reader/writer access. Use `db.transaction()` context manager for multi-statement atomic operations (e.g. batch imports).
4. **HTTP** (`core/client/http_client.py`): SSL verification on by default, rate-limiting, timeout enforcement (0.1–300 s), max 10 redirects, configurable retry policy (`RetryPolicy`), concurrency control (`ConcurrencyGuard`).
5. **OAuth2** (`auth/oauth2.py`): `to_dict()` includes `client_secret` and `token_timeout` for round-trip storage — the DB layer encrypts it via `auth_cipher`. Tokens auto-refresh 30 s before expiry.

## Development Workflows

### Testing

```bash
pip install -e ".[dev]"

# Full suite
pytest

# Targeted
pytest tests/storage/test_migrations.py        # migration system
pytest tests/importers/                         # multi-server OpenAPI + Postman vars
pytest tests/core/test_security_comprehensive.py  # security tests
pytest tests/core/test_auth_encryption.py      # auth encryption boundary (if present)
pytest tests/storage/test_request_persistence.py  # autosave & path_params round-trip

# With coverage
pytest --cov=equinox --cov-report=html
```

**Test directory layout**: Tests are organized to mirror the source tree — `tests/storage/`, `tests/core/`, `tests/core/client/`, `tests/auth/`, `tests/importers/`, `tests/gui/`, `tests/intelligence/`, `tests/plugins/`, `tests/security/`, `tests/flags/`.

### Running the app

```bash
python -m equinox.gui.app  # PyQt6 GUI bootstrap
equinox rotate-secrets --db-path ./equinox.db  # rotate plaintext secrets to enc: blobs
```

### CI & Developer Hygiene

- Keep CI aligned with local checks: Ruff, Mypy, tests, and security/static checks as configured.
- Use `pre-commit` locally and keep hooks in sync with CI behavior.
- Keep agent-facing docs aligned with the actual command set in `README.md`.

### Boundary placement rules for new code

- Put request orchestration/business rules in `application/requests/` modules.
- Keep GUI modules presentation-only (widget state, event wiring, rendering).
- GUI modules must not construct storage managers directly in request/history/collection flows.
- Use `gui/error_presenter.py` for user-visible error dialogs instead of ad-hoc `QMessageBox` formatting.

### Developer Workflow & Contribution Quick Links

**Getting started?**

- Read [DEVELOPMENT.md](DEVELOPMENT.md) for step-by-step workflow and common tasks
- Read [CONTRIBUTING.md](CONTRIBUTING.md) for code standards and architecture principles
- This file (`AGENTS.md`) provides deep-dive architecture details

**Before committing code:**

```bash
pre-commit run --all-files  # Format, lint, type-check, security scan
python scripts/run_affected_tests.py  # Fast changed-module test/coverage gate
python scripts/manage_requirements_lock.py --check  # Validate lockfile consistency (no writes)
python scripts/check_dependency_vulnerabilities.py  # Blocking dependency CVE gate
pytest --no-cov             # Run tests (fast)
```

`requirements-lock.txt` is a committed artifact. Use `scripts/manage_requirements_lock.py --write` only when intentionally regenerating lock artifacts.

**Before opening a PR:**

```bash
pytest --cov=equinox --cov-report=html  # Full coverage check
# Review htmlcov/index.html for coverage gaps
```

## Project-Specific Conventions

### Variable interpolation (`{{VAR}}` syntax)

Used everywhere — URLs, headers, params, body. Resolution order:
1. Collection-level variables (imported from Postman/OpenAPI)
2. Active database environment (`EnvironmentManager.get_active_environment()`)
3. OS environment variables

Unresolvable tokens are **left as-is** (never silently dropped).

The shared implementation is `core/interpolation.py` → `VariableInterpolator` and `collect_interpolation_variables`. Both the GUI and CLI delegate to this module.

### URL handling (`core/urls/` package)

`core/urls/` is a package split across three focused modules:

- **`parsing.py`** — Low-level URL parsing: `URLComponents`, `url_metadata()`, `parse_query_pairs()`. Selects `urlps` when available; falls back to `urllib.parse` via `_parse_url` selected at module load.
- **`normalizer.py`** — URL normalization: `expand_placeholders(url, variables)`, `normalized_parts(url, variables)`, `normalize_url(url, variables)`, `base_path(normalized_url)`.
- **`utils.py`** — Convenience helpers: `append_query_params()`, `join_url_path()`.

The package `__init__.py` re-exports all public functions so existing imports (`from equinox.core.urls import normalize_url`) continue to work unchanged. Use `from equinox.core.urls import …` as the canonical import path.

### Interceptors

`core/interceptors/` exposes `RequestInterceptor`, `ResponseInterceptor`, `ErrorInterceptor` base classes and an `InterceptorChain`. Add interceptors to `client.interceptors` before calling `client.send()`.

### Auth serialization

All auth classes implement `to_dict()` / `from_dict()`. `OAuth2Auth.to_dict()` includes `client_secret` and `token_timeout` (needed for storage round-trip). Display-only contexts should omit secrets manually. The `_serialize_auth()` / `_deserialize_auth()` boundary in `storage/collections/auth.py` encrypts all auth data via `core/auth_cipher.py` before writing to SQLite. Legacy plaintext rows are read transparently.

AWS SigV4 auth is available via `auth/aws_sigv4.py` (`AWSSigV4Auth`). Auth instances are constructed via `auth/factory.py`.

### Secret manager integration

External secret-manager backends live in `core/secret_managers/` and are created via `get_secret_manager()` in `core/secret_managers/registry.py`.

- Built-in manager aliases include `env`, `aws`/`aws_secrets_manager`, `vault`, and `bitwarden`.
- `storage/secret_integration.py` (`CredentialSecretResolver`) hydrates saved credentials from external secret sources before auth strategy construction.
- GUI secret-manager connection profiles are persisted by `storage/secret_manager_configs.py` (`SecretManagerConfigStore`) at `~/.equinox/secret_managers.json`.

### Security facade and redaction

Use `security/__init__.py` as the public security facade for cross-module imports (`redact_headers`, `redact_url`, `redact_body`, key helpers, `SecureStorage`) so redaction and crypto behavior stays centralized.

### Feature flags (`core/config/flags.py`)

Environment-toggled behavior is centralized in `core/config/flags.py`:
- `EQUINOX_USE_OS_KEYRING` → `is_os_keystore_enabled()`
- `EQUINOX_HISTORY_CAPTURE_BODIES` → `is_history_capture_enabled()`
- `EQUINOX_TRACK_UI_USAGE` → `is_ui_usage_tracking_enabled()` (v0.4.3+)

### Assertions and captures

- **Assertions** (`core/assertions.py`): `evaluate_assertion(rule, response)` tests a single rule dict against a response. Rules are stored as a JSON array in `requests.assertions`.
- **Captures** (`core/captures.py`): `CaptureEngine.apply_all()` extracts values from a response into session variables. Sources: `json` (dot-notation path), `header`, `regex`, `status`. Results stored in `requests.captures`.

### Pre/post scripts

`core/scripts/` provides a sandboxed Python script runner. Scripts run in a restricted environment with only safe builtins and a fixed allowlist of stdlib imports. Available context: `request`, `response`, `env` (mutable — keys flow into `{{var}}` interpolation for the next request). Scripts stored in `requests.pre_script` and `requests.post_script`.

### Code generation

`core/codegen/` converts a `Request` object to client code in multiple languages. Used by `ResponsePanel` and the CLI.

### cURL import

`core/io/curl_parser.py`: `parse_curl(curl_cmd)` parses a cURL command string into a dict suitable for building a `Request`. Import via `from equinox.core.io import parse_curl` (preferred) or the legacy path `from equinox.core.curl_parser import parse_curl` (still resolves).

### Autosave and request persistence

`RequestPanel.autosave_current()` persists dirty editor state through `RequestPersistenceFacade`. It runs automatically before switching requests, loading history, closing the window, or creating a new request. Only acts when the loaded request has a DB `id` (collection request); ad-hoc / history requests are silently skipped.

**Critical rules for the dirty flag**:
- `_send_request()` must **never** call `_clear_dirty()`. Sending is not a save — the user's edits need to survive autosave when switching away. The dirty flag stays True so `autosave_current()` writes to the DB.
- `_clear_dirty()` is called at the end of `load_request()` (after all widgets are populated) and after a successful `_save_request()` (Save to Collection…).
- `_save_request()` (the "Save to Collection…" dialog flow) must update `self.current_request` with the returned DB `id` and `collection_id` so subsequent autosaves target the correct row.

**Important**: `_send_request()` bakes `effective_auth` (which may be inherited) into `self.current_request.auth`, but `autosave_current()` always reads `self._auth` (own auth only) so inherited auth is never accidentally persisted onto the request row.

**path_params**: Facade-backed save/update flows persist the `path_params` JSON column, and storage hydration reads it back. Keep both sides in sync when adding new request fields.

### GUI logging

The `LoggingPanel` (`gui/logging_panel.py`) is wired to `RequestPanel` via parent-widget inspection:
```python
win = self.window()
if hasattr(win, 'logging_panel'):
    win.logging_panel.log_request(request)    # on send
    win.logging_panel.log_response(request, response)  # on success
    win.logging_panel.log_error(request, error_msg)     # on failure
```

### GUI list+form dialogs (blocked-signals pattern)

`OAuthClientsDialog`, `SavedCredentialsDialog`, and `EnvironmentDialog` all use a split-pane (list + edit form) pattern. Common infrastructure is extracted into `gui/dialogs/_dirty_dialog_mixin.py` → `DirtyDialogMixin`, which provides:

- `_on_close()` — dirty close guard
- `_reselect_item(item_id)` — re-select by ID without firing signals
- `_prompt_unsaved(current_id)` — dirty-state prompt before switching
- `_format_status(msg, ok)` — coloured HTML status label helper

Subclasses must set `_dirty`, `_list_widget`, and `_save_callback` before calling these helpers.

The `_refresh_list(select_id)` / `_apply_selection()` signal-blocking pattern still applies within each dialog:

1. `_refresh_list(select_id)` rebuilds the list with `blockSignals(True)`. If `select_id` is given, that item is selected *while signals are blocked*.
2. After unblocking: if `select_id is None`, call `setCurrentRow(0)` to fire `_on_item_selected` normally. Otherwise call `_apply_selection()` to drive form loading manually (since the signal didn't fire).
3. `_apply_selection()` reads the current list item, loads the form, resets `_dirty = False`, and enables/disables buttons — without prompting about unsaved changes.
4. `_on_item_selected()` includes a same-id guard (`if new_id == self._current_id: return`) and dirty-state prompt before switching.
5. All CRUD methods must set `_dirty = False` **before** calling `_refresh_list()`.

### Updating request auth (CLI)

Use `CollectionManager.update_request_auth(request_id, auth_obj)` to modify auth in-place on an existing request. **Do not** use `save_request()` for auth-only updates — that creates a duplicate row.

### Response body storage

`Response.body` is `bytes` (from httpx), but history stores it as decoded text. When reconstructing a `Response` from history (e.g. `_load_history_entry` in `window.py`), encode the DB string back to bytes:
```python
raw_body = entry.get("response_body") or ""
body_bytes = raw_body.encode("utf-8") if isinstance(raw_body, str) else (raw_body or b"")
```

### Python version compatibility

`pyproject.toml` declares `requires-python >= 3.10`. Modern runtime annotations like `int | None` are supported; quote annotations only when needed for forward references.

### Syntax highlighting (centralized, package)

Syntax highlighting is implemented centrally in the `gui/syntax_highlighter/` **package**.

- **`base.py`**: `RegexHighlighterBase(QSyntaxHighlighter)`, `RegexRule`, `_make_format()`, `_variable_fmt()`, `_VARIABLE_PATTERN`
- **`json_highlighter.py`**: `JsonHighlighter`
- **`python_highlighter.py`**: `PythonHighlighter`
- **`xml_highlighter.py`**: `XmlHighlighter` (alias `HtmlHighlighter`)
- **`yaml_highlighter.py`**: `YamlHighlighter`
- Public `__init__.py` exports `JsonHighlighter`, `XmlHighlighter`, `YamlHighlighter`. Import `PythonHighlighter` directly from its module when needed.

Theme integration: `gui/theme/` (via `gui/theme/__init__.py`) exposes `Colors` and `get_mono_font()` used by the highlighters.

### Test isolation — history body capture toggle

`core/history_config.py` stores capture mode in process-global state. Tests that call `set_capture_bodies(False)` must reset it (typically in teardown/finalizer) via `set_capture_bodies(True)` to avoid leaking state into unrelated history tests.

Where highlighters attach:
- `JsonBodyEditor` (`gui/widgets/json_body_editor.py`) attaches `JsonHighlighter` to its document internally — do **not** add a second highlighter on the same document.
- `ResponsePanel` uses `_apply_highlighter()` to dynamically attach the correct highlighter.
- `RequestPanel` attaches `PythonHighlighter` for script editors.

Guidance for adding a new language highlighter:

1. Add a new subclass of `RegexHighlighterBase` in `src/equinox/gui/syntax_highlighter/` (new file) and implement `_build_rules()` returning `RegexRule(pattern, fmt)` entries.
2. Export it from `__init__.py`.
3. Reuse `_make_format(...)` and `Colors` for consistent styling.
4. Attach the highlighter to the editor's `QTextDocument` when creating the editor.
5. If dynamic (e.g. ResponsePanel), add a branch in `ResponsePanel._apply_highlighter()`.

### History system (package)

`storage/history/` is a package. Responsibilities are split:

- **`manager.py`** (`HistoryManager`): Public API only — `save_history`, `delete_history`, `clear_history`, `get_history`, `list_history`, `search_history`, `get_stats`.
- **`_serializer.py`** (`_HistorySerializer`): Converts `Request`/`Response` objects to storable dicts and decodes DB rows back. Owns size constants (`MAX_BODY_SIZE`, `MAX_HEADERS_SIZE`, `MAX_URL_LENGTH`, `MAX_ERROR_MESSAGE_LENGTH`).
- **`_indexer.py`** (`_HistoryIndexer`): Maintains the `history_index` fast-lookup table. Called from `HistoryManager.save_history()` after each insert.
- **`_searcher.py`** (`_HistorySearcher`): Builds SQL WHERE clauses and applies Python post-filters for `search_history`. Owns `MAX_REGEX_LENGTH` and `MAX_LIMIT`.
- **`_constants.py`**: Shared constants (`_LIKE_ESCAPE_CLAUSE`, `_STATUS_CODE_RANGES`).

`HistoryManager` does **not** have `_prepare_url` or `_index_history_row` methods — those concerns live in `_indexer.py`.

### Response Intelligence (package)

`core/response_intelligence/` is a package providing endpoint statistics, schema drift tracking, and security/performance hints. Stored via `storage/response_intelligence.py`.

The intelligence panel (`gui/intelligence_panel.py`) and its background worker (`gui/intelligence_worker.py`) surface these insights in the GUI.

### Request Recommender

`intelligence/recommender.py` → `Recommender` queries the history DB for structurally similar past requests and generates confidence-ranked header and query-param suggestions. Similarity is scored across five dimensions: method (0.3), path (0.4), query (0.1), headers (0.1), body (0.1).

### UI Usage Tracking System (v0.4.3+)

`storage/ui_usage_tracker.py` provides transparent local-only UI interaction analytics:

- **`UIUsageTracker`**: Main service class that tracks user interactions (command palette, menu items, tab navigation, environment switching)
- **Data Collected**: Action name, timestamp, frequency count, last used timestamp (stored in SQLite `ui_usage` table)
- **Privacy**: Data stored locally only; no external transmission
- **Control**: Disabled via `EQUINOX_TRACK_UI_USAGE=0` environment variable
- **Ranking Algorithm**: Combines frequency + recency + priority (active state takes precedence)

**Usage Pattern:**
```python
from equinox.storage.ui_usage_tracker import UIUsageTracker

tracker = UIUsageTracker(db)
tracker.track_action("command_send_request", metadata={"collection": "demo"})
ranked_items = tracker.get_ranked_items("command_palette", limit=10)
```

The tracking system powers:
- **Command Palette Ranking**: Most-used commands appear first in search results
- **Context Menu Ranking**: Frequently-selected items appear at top
- **Environment Menu Ranking**: Active environment always at top, recently-used below
- **Destructive Action Separation**: Delete/clear actions separated at bottom with visual separator

**Associated GUI Components:**
- `gui/dialogs/usage_stats_dialog.py`: User-accessible interface to view and manage usage statistics
- `gui/command_palette.py`: Auto-ranks suggestions by usage
- `gui/dialogs/context_menu.py`: Ranks actions by frequency
- `gui/dialogs/environment_menu.py`: Ranks environments by usage and active state

### Keyboard Shortcuts and Navigation (v0.4.3+)

`gui/window.py` and `gui/sidebar.py` implement keyboard-driven navigation:

- **Shortcuts**: `Ctrl+1` through `Ctrl+8` navigate between sidebar tabs
- **Implementation**: Qt `QShortcut` objects registered at main window level
- **Conflict Prevention**: Shortcuts only activate when focus is on neutral UI (tab bar, status bar, not text editors)
- **Tab Mapping**:
  - `Ctrl+1`: Request Builder
  - `Ctrl+2`: Response Viewer
  - `Ctrl+3`: Collections
  - `Ctrl+4`: Variables
  - `Ctrl+5`: History
  - `Ctrl+6`: Logs
  - `Ctrl+7`: Intelligence
  - `Ctrl+8`: WebSocket

### Worker Thread Management (v0.4.2+)

**Intelligence Worker Improvements:**
- `gui/intelligence_worker.py` now includes defensive parent widget checks
- Workers validate parent object existence before accessing it
- Prevents crashes when intelligence panel is closed during analysis
- Cancellation events logged at INFO level for better observability

**Worker Cancellation Pattern:**
```python
def run(self) -> None:
    try:
        if not self.parent or not self.parent.isVisible():
            logger.info("Parent widget no longer valid, cancelling")
            return
        # ... perform work ...
    except Exception as exc:
        logger.error("Worker error", extra={"error": str(exc)})
```

## Key Files

| File | Purpose |
|------|---------|
| `storage/migrations.py` | Versioned schema — **only place to change the DB schema** (currently at v24) |
| `storage/schema.sql` | Reference schema (documentation only — not used at runtime) |
| `storage/ui_usage_tracker.py` | `UIUsageTracker` — local UI analytics and action ranking (v0.4.3+) |
| `storage/collections/manager.py` | `CollectionManager` — `save_request`, `update_request`, `update_request_auth`, CRUD |
| `storage/collections/auth.py` | `_serialize_auth` / `_deserialize_auth` — Fernet encryption boundary |
| `storage/collections/folders.py` | `CollectionFoldersMixin` — folder CRUD |
| `storage/collections/ordering.py` | `CollectionOrderingMixin` — `sort_order` / move_request |
| `storage/collections/variables.py` | `CollectionVariablesMixin` — collection-scoped variables |
| `storage/history/manager.py` | `HistoryManager` — public history API |
| `storage/history/_indexer.py` | `_HistoryIndexer` — `history_index` table maintenance |
| `storage/history/_searcher.py` | `_HistorySearcher` — SQL filter construction and post-filters |
| `storage/history/_serializer.py` | `_HistorySerializer` — request/response ↔ DB row conversion |
| `storage/saved_credentials.py` | `SavedCredentialsManager` — unified multi-type credential storage |
| `storage/secret_integration.py` | `CredentialSecretResolver` — resolve credential values from external secret managers |
| `storage/secret_manager_configs.py` | `SecretManagerConfigStore` — persist GUI secret-manager connection profiles |
| `storage/variable_groups.py` | `VariableGroupManager` — named variable groups |
| `storage/database.py` | `Database` — SQLite ORM; `.lock` is a **read-only property** |
| `importers/openapi.py` | Multi-server resolution helpers (`ServerInfo`, `_resolve_servers_openapi3`, `_resolve_servers_swagger2`) |
| `importers/postman.py` | `_extract_collection_variables`, `_resolve_postman_variable` |
| `exporters/` | Standalone exporters package: `postman.py`, `openapi.py`, `insomnia.py`, `har.py`, `curl.py` |
| `gui/dialogs/saved_credentials_dialog.py` | Reference for the blocked-signals list+form dialog pattern |
| `gui/dialogs/_dirty_dialog_mixin.py` | `DirtyDialogMixin` — shared dirty-state infrastructure for list+form dialogs |
| `gui/dialogs/context_menu.py` | Ranked context menu with usage-based sorting and destructive action separation (v0.4.3+) |
| `gui/dialogs/usage_stats_dialog.py` | UI usage statistics viewer and management interface (v0.4.3+) |
| `gui/request_panel/` | Package: `panel.py` (RequestPanel), `mixins.py` (send/auth), `body_mixin.py`, `builder.py`, `save_dialog.py`, `toolbar.py` |
| `gui/response_panel/` | Package: `panel.py`, `builder.py`, `display_mixin.py`, `actions_mixin.py`, `search_bar.py`, etc. |
| `gui/syntax_highlighter/` | Package: `base.py`, `json_highlighter.py`, `python_highlighter.py`, `xml_highlighter.py`, `yaml_highlighter.py` |
| `gui/intelligence_worker.py` | Background intelligence analysis with defensive parent widget checks (v0.4.2+) |
| `gui/window.py` | Signal wiring hub — connects all panels, menu actions, and keyboard shortcuts |
| `core/interceptors/` | `InterceptorChain`, logging interceptors |
| `core/log_setup.py` | JSON structured logging to `~/.equinox/logs/equinox.log` |
| `core/config/flags.py` | Environment-based feature toggles (`EQUINOX_USE_OS_KEYRING`, `EQUINOX_HISTORY_CAPTURE_BODIES`) |
| `core/util/` | Utility sub-package: `time.py` (`utc_now`, `to_iso_z`), `constants.py` (size-limit constants) |
| `core/format/` | Error sub-package: `error_enrichment.py` (`enrich_exception`, `RichError`), `error_mapper.py` (`build_error_handlers`) |
| `core/http/` | HTTP helpers sub-package: `cookies.py` (`CookieManager`), `rate_limiter.py` (`RateLimiter`), `proxy.py` (`check_proxy_reachable`) |
| `core/io/` | I/O sub-package: `curl_parser.py` (`parse_curl`), `dotenv.py` (`parse_dotenv`), `multipart.py` |
| `core/urls/` | URL package: `parsing.py` (low-level parse), `normalizer.py` (`expand_placeholders`, `normalize_url`, `normalized_parts`, `base_path`), `utils.py` helpers |
| `core/secret_managers/` | Secret manager backends + registry (`get_secret_manager`, `register_manager`) |
| `core/auth_cipher.py` | Column-level Fernet encryption for `auth_data` / `config` columns |
| `core/assertions.py` | `evaluate_assertion(rule, response)` — post-response test rules |
| `core/captures.py` | `CaptureEngine` — extract response values into session variables |
| `core/scripts/` | Sandboxed Python script runner (pre/post request scripts) |
| `core/codegen/` | `generate_*` functions — Request → client code in multiple languages |
| `core/interpolation.py` | `VariableInterpolator`, `collect_interpolation_variables` |
| `core/client/http_client.py` | `HTTPClient` — main façade; delegates to pipeline components |
| `core/client/pipeline.py` | `RequestPipeline` — interceptor chain + audit wrapper |
| `core/client/dispatcher.py` | `HttpxDispatcher` — httpx transport layer |
| `core/client/retry_policy.py` | `RetryPolicy` — timeout + HTTP overload retries |
| `core/client/concurrency_guard.py` | `ConcurrencyGuard` — max-concurrent-requests slot |
| `core/validation/__init__.py` | `Validator` façade — always start here for new input types |
| `auth/oauth2.py` | `OAuth2Auth` with auto-refresh and HTTP Basic auth fallback (v0.4.2+); tokens encrypted at DB layer |
| `auth/aws_sigv4.py` | `AWSSigV4Auth` — pure-Python AWS Signature V4 signing |
| `auth/factory.py` | Auth instance factory |
| `security/__init__.py` | Public security facade for redaction + crypto helpers + `SecureStorage` |
| `cli/main.py` | Click operational entrypoint (`rotate-secrets`) |
| `intelligence/recommender.py` | `Recommender` — confidence-ranked header/param suggestions |
| `scripts/benchmark_history_search.py` | Performance benchmark harness for history search (v0.4.2+) |
| `tests/storage/test_migrations.py` | Reference for how to test new migrations |
| `tests/gui/test_keyboard_shortcuts.py` | Keyboard shortcut registration and handling tests (v0.4.3+) |
| `tests/gui/test_context_menu_ranking.py` | Context menu ranking and destructive action tests (v0.4.3+) |
| `tests/storage/test_ui_usage_tracker.py` | UI usage tracking and ranking algorithm tests (v0.4.3+) |
| `tests/storage/test_history_search_performance.py` | Performance regression tests for history search (v0.4.2+) |
| `tests/gui/test_worker_cancellation.py` | Worker thread cancellation and cleanup tests (v0.4.2+) |
| `tests/importers/` | Reference for importer tests |
| `tests/core/test_security_comprehensive.py` | Reference for security tests |
| `tests/core/test_validation_properties.py` | Property-based validation tests using Hypothesis (v0.4.2+) |
| `tests/security/` | Secret rotation, redaction, keystore integration, and master-password prompt tests |
| `tests/flags/test_flags.py` | Environment flag behavior tests |
| `tests/storage/test_request_persistence.py` | Reference for autosave & path_params round-trip tests |
