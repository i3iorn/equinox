# AGENTS.md — Equinox

Local-first API testing tool (GUI + CLI). Source at `src/equinox/`; package import is `equinox`, distribution name is `equinox-api` (`pyproject.toml`). Read `README.md`, `DEVELOPMENT.md`, `WORKFLOW.md`, `CONTRIBUTING.md` for full docs.

## Setup & Key Commands

```bash
pip install -e ".[dev]"
pre-commit install
```

Non-obvious commands (verified in repo):
- `python scripts/run_affected_tests.py` — fast gate for changed modules
- `python scripts/check_dependency_vulnerabilities.py` — blocking dependency CVE gate
- `python scripts/benchmark_history_search.py --entries 5000 --runs 20` — performance baseline
- `equinox rotate-secrets --db-path ./equinox.db` — rotate plaintext secrets to `enc:` blobs
- `python -m equinox.gui.app` — GUI bootstrap; `equinox gui` is equivalent CLI entry
- Branch target for PRs: `dev` (integration branch), not `master` (release-only)

Full quality gate (matches CI):
```bash
pre-commit run --all-files          # format, lint, type-check, security, dependency check
ruff check . && ruff format --check .
mypy src tests                      # strict=true, platform=linux (must match CI)
bandit -r src/equinox --severity-level=medium -s "B102,B113,B318,B608"
python scripts/check_dependency_vulnerabilities.py
pytest --cov=equinox --cov-report=html
```

## Critical Conventions

- **Migrations only**: Add `Migration` entries to `storage/migrations.py` (current v25). Never edit `storage/schema.sql` (reference only). Runner runs automatically in `Database.__init__`.
- **Database `.lock` is read-only** (`@property`). For bare instances set `db._lock` directly.
- **Coverage is honest**: `fail_under = 74` in `pyproject.toml`. Do not restore old `omit = ["gui/", "plugins/"]` to inflate numbers; they measure the full project now.
- **Mypy strict + linux platform**: `pyproject.toml` sets `strict = true`, `platform = linux`. Local `mypy src` must match CI exactly.

## Security & Auth Boundaries

- **Input validation facade**: Use `Validator` (`core/validation/__init__.py`) for all untrusted inputs. Zero-trust; internal components also validated.
- **Plugin trust model**: Plugins are trusted local, in-process extensions. Permission/checksum/allowlist are policy, not isolation sandbox.
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

- History stores response body as decoded text. When reconstructing `Response` from history (`gui/window/_history.py`), encode back: `raw_body.encode("utf-8")`.
- `core/history_config.py` uses process-global state (`set_capture_bodies`). Tests that disable it must reset (`set_capture_bodies(True)`) in teardown or they leak across tests.
- History package split: `manager.py` (public API), `_serializer.py` (DB conversion + size limits), `_indexer.py` (`history_index` table), `_searcher.py` (SQL filters + post-filters). `HistoryManager` has no `_prepare_url` or `_index_history_row`.

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
