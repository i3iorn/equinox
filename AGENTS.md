# AGENTS.md — Equinox

Local-first API testing tool (GUI + CLI). Source at `src/equinox/`; package import is `equinox`, distribution name is `equinox-api` (`pyproject.toml`). Read `README.md`, `DEVELOPMENT.md`, `WORKFLOW.md`, `CONTRIBUTING.md` for full docs.

## Setup & Key Commands

```bash
pip install -e ".[dev]"
pre-commit install
```

Non-obvious commands (verified in repo):
- `python scripts/run_affected_tests.py` — pre-push gate; maps changed `src/equinox/<dir>/<name>.py` to `tests/<dir>/test_<name>.py` (leading `_` stripped: `core/_history.py` → `tests/core/test_history.py`) and restricts coverage to just those source files. A new module with no mirror test silently runs zero tests (prints "No matching tests found", exits 0), so add its test in the same commit
- `python scripts/check_dependency_vulnerabilities.py` — blocking dependency CVE gate (CI also runs `check_dependency_consistency.py` and `verify_ci_toolchain.py`)
- `python scripts/benchmark_history_search.py --entries 5000 --runs 20` — performance baseline
- `equinox rotate-secrets --db-path ./equinox.db` — rotate plaintext secrets to `enc:` blobs
- `python -m equinox.gui.app` — GUI bootstrap; `equinox gui` is equivalent CLI entry
- Branch target for PRs: `dev` (integration branch), not `master` (release-only; `no-commit-to-branch` blocks local commits on `master`)

### Testing

- `pytest` runs coverage by default (`addopts` in `pyproject.toml`). Use `pytest --no-cov` for a fast run, `pytest --cov=equinox --cov-report=html` for the PR check.
- GUI tests need headless Qt: set `QT_QPA_PLATFORM=offscreen` (CI does this; without it Qt tries to open a display).
- CI matrix is Python 3.10–3.14 (not just 3.12 as some older docs say).
- Older docs (README/WORKFLOW) cite 87% coverage — stale. The enforced floor is `fail_under = 74` and CI's `--cov-fail-under=74`; keep those two in sync.

Full quality gate (matches CI):
```bash
pre-commit run --all-files          # hygiene, size, security, dependency checks (see caveats below)
ruff check src/ tests/ && ruff format --check src/ tests/   # CI scopes to src/ and tests/, not repo root
mypy src                            # strict=true, platform=linux; CI's typecheck gate runs `mypy src` only (tests aren't type-checked in CI)
bandit -r src/equinox --severity-level=medium -s "B102,B113,B318,B608"
python scripts/check_dependency_vulnerabilities.py
pytest --cov=equinox --cov-report=html
```

Pre-commit caveats (a green `pre-commit run` can still fail CI):
- The `ruff` hook runs `--fix --exit-zero`, so lint errors are auto-fixed or swallowed — pre-commit passing does **not** mean `ruff check` is clean. CI's `ruff check src/ tests/` (no `--exit-zero`) is the real gate.
- `detect-secrets` scans against `.secrets.baseline`; a new false positive fails the hook. Refresh the baseline (`detect-secrets scan --baseline .secrets.baseline`) or use an inline allowlist comment — never commit the "secret".
- `pyupgrade` is pinned to `--py310-plus` to match the `requires-python` floor. Raising it emits syntax invalid on 3.10/3.11, which CI still tests.
- `check_code_component_size.py` enforces hard limits (module 1000 / function 120 / class 800 lines — the *hard* limits, not the softer aspirational ones in CONTRIBUTING); `mypy-strict` runs only at pre-commit and `run-affected-tests` only at pre-push.

## Critical Conventions

- **Migrations only**: Add `Migration` entries to `storage/migrations.py` (current v25). Never edit `storage/schema.sql` (reference only). Runner runs automatically in `Database.__init__`.
- **`Database.lock` is a read-only `@property`** returning the internal `_lock` mutex. Tests that build a bare `Database` without `__init__` set `db._lock = threading.Lock()` directly (see `tests/storage/test_migrations.py`).
- **Coverage is honest**: `fail_under = 74` in `pyproject.toml`. Do not restore old `omit = ["gui/", "plugins/"]` to inflate numbers; they measure the full project now.
- **Mypy strict + linux platform**: `pyproject.toml` sets `strict = true`, `platform = linux`. Local `mypy src` must match CI exactly.

## Security & Auth Boundaries

- **Validation vs display sanitization**: `Validator` (`core/validation/__init__.py`) validates all untrusted inputs (zero-trust; internal components validated too). Display text goes through `Sanitizer.sanitize_for_display()` — it was moved off `Validator`, so `Validator.sanitize_for_display` no longer exists.
- **SSRF guard**: `Validator.validate_resolved_url(url, tolerate_dns_failure=True)` relaxes *only* the DNS-resolution step (used by the Vault backend's explicit insecure-http mode). Literal private IPs and cloud-metadata hostnames are never allowed; the compat flag `is_ssrf_allow_on_dns_failure_enabled()` does the same fail-open globally.
- **Plugin trust model**: Plugins are trusted local, in-process extensions. Permission/checksum/allowlist are policy, not an isolation sandbox. Separately, plugin *scripts* run through an AST allowlist in `src/equinox/plugins/security.py` (`PluginSandbox`, `_find_dangerous_imports`/`_find_dangerous_functions`) — note the repo-root `plugins/` dir is empty; when extending the allowlist, remember attribute calls (`obj.eval()`), subscript keys (`d["__import__"]`), and `from os import system` all bypass naive bare-Name checks.
- **Auth encryption at rest**: `_serialize_auth()` / `_deserialize_auth()` in `storage/collections/auth.py`. Values encrypted with Fernet; `enc:` prefix for encrypted blobs; legacy plaintext fallback exists. `core/auth_cipher.py` is compatibility facade; primitives in `security/auth_cipher.py`; storage helpers in `storage/auth_cipher_storage.py`.
- **Secret managers**: Built-in aliases: `env`, `aws`/`aws_secrets_manager`, `vault`, `bitwarden`. Profiles persisted at `~/.equinox/secret_managers.json`.
- **Public security facade**: `security/__init__.py` (`redact_headers`, `redact_url`, `redact_body`, `SecureStorage`).

## Request Persistence & Autosave (High-Mistake Area)

- `_send_request()` must **never** call `_clear_dirty()`. Sending is not a save; dirty flag must stay `True` so `autosave_current()` writes to DB when switching.
- `_clear_dirty()` is called after `load_request()` and after successful `_save_request()`.
- `_save_request()` must update `self.current_request` with returned DB `id` and `collection_id`.
- `_send_request()` bakes `effective_auth` (inherited) into `self.current_request.auth`, but `autosave_current()` reads `self._auth` (own auth only) — inherited auth is never accidentally persisted.
- `path_params` must be kept in sync on both save/update sides.

## History & Response Reconstruction

- History stores response body as decoded text. Reconstruction lives in `application/history/facade.py` (`HistoryFacade.request_from_entry` / `response_from_entry`); `gui/window/_history.py` only delegates to it. The body is re-encoded to bytes via `_coerce_body_to_bytes` (`raw.encode("utf-8")`) before building `Response`.
- `core/history_config.py` uses process-global state. Tests that disable body capture must call `reset_capture_bodies()` in teardown or the override leaks across tests.
- History package split (`storage/history/`): `manager.py` (public API), `_serializer.py` (DB conversion + size limits), `_indexer.py` (`history_index` table), `_searcher.py` (SQL filters + post-filters). `HistoryManager` has no `_prepare_url` (it lives in `_serializer.py`) or `_index_history_row`.

## Importer Behavior

- **OpenAPI multi-server**: `_resolve_servers_openapi3()` expands variables from defaults, strips trailing `/`, falls back to `/`. N servers = N collections, each named `"<title> — <description>"` with a `BASE_URL` variable. Relative URLs promoted to `https://{{BASE_URL}}`.
- **Postman variables**: `_resolve_postman_variable()` substitutes known `{{key}}`; unknown tokens left as-is for runtime environment resolution.

## GUI / Storage Boundaries

- GUI modules are presentation-only (widget state, event wiring, rendering).
- Do not construct storage managers directly inside GUI panels/mixins; use application facades (`application/requests/` for orchestration/business rules).
- User-visible errors should go through `gui/error_presenter.py`, not ad-hoc `QMessageBox`.
- `RequestPanel.autosave_current()` persists dirty state through `RequestPersistenceFacade`; only acts on requests with a DB `id` (collection requests). Ad-hoc/history requests skipped silently.

## Key Wiring References

- Main entry: `gui/app.py` (GUI), `cli/main.py` (CLI)
- HTTP pipeline: `core/client/http_client.py` (facade), `pipeline.py` (interceptor chain + audit), `dispatcher.py` (httpx)
- URL package: `core/urls/` (`parsing.py`, `normalizer.py`, `utils.py`); import via `core/urls`
- Syntax highlighting package: `gui/syntax_highlighter/` (`base.py`, `json_highlighter/`, `python_highlighter.py`, etc.). `JsonBodyEditor` attaches `JsonHighlighter` internally — don't double-attach.
- Intelligence worker (`gui/intelligence_worker.py`): checks parent widget visibility before access; cancellation logged at INFO.
