# Changelog — Equinox v0.6.0

**Release Date:** October 19, 2026

## Overview

Equinox v0.6.0 is a security-hardening release. It closes three classes of
vulnerability that survived earlier audits — server-side request forgery via
HTTP redirects, introspection escapes from the script sandbox, and silent key
replacement when the OS keychain or a master-password loader fails — and moves
display sanitization onto its own `Sanitizer` API.

Every fix is fail-closed: where the previous behavior guessed, the new behavior
refuses. The two behavior changes to plan for are listed under Upgrade Notes.

---

## [0.6.0] — 2026-10-19

### Security & Input Validation

#### Fixed: SSRF guard skipped on redirect hops
- **Files:** `src/equinox/core/client/dispatcher.py`, `src/equinox/core/validation/__init__.py`, `src/equinox/core/validation/_ssrf.py`, `src/equinox/core/validation/_url.py`, `src/equinox/core/secret_managers/vault.py`, `tests/core/client/test_dispatcher_ssrf_redirect.py`
- **Change:** httpx resolves redirects internally, so a URL that passed validation at send time could redirect the client to an internal address that was never checked. The shared client now installs a per-hop request hook that re-runs `Validator.validate_resolved_url()` on every URL the transport is about to touch. `validate_resolved_url()` gained a keyword-only `tolerate_dns_failure` flag that relaxes *only* the DNS-resolution step, and the Vault backend's explicit insecure-http mode now relaxes only loopback and DNS failure instead of skipping the guard entirely.
- **Benefit:** Redirecting a public URL to `169.254.169.254` no longer smuggles requests past the guard, and the `X-Vault-Token` header cannot leak to an internal host in insecure mode. Metadata hostnames and literal private IPs remain always-rejected regardless of the flag.

#### Fixed: Script sandbox escaped through introspection
- **Files:** `src/equinox/core/scripts/sandbox.py`, `src/equinox/core/scripts/validation.py`, `src/equinox/plugins/security.py`, `tests/core/test_scripts_module_escape.py`
- **Change:** The module-proxy denylist and the AST scan now agree on a much larger forbidden surface — class traversal (`__base__`, `__bases__`, `__mro__`, `__subclasses__`), namespaces (`__globals__`, `__dict__`, `__builtins__`), frame and traceback introspection (`tb_frame`, `f_builtins`, `f_globals`), generator/coroutine frames, and loader objects (`__loader__`, `__spec__`, `get_data`). The plugin AST allowlist additionally rejects `from os import system`, attribute calls (`obj.eval()`), and literal-subscript calls (`d["__import__"]`), which bare-`Name` checks never saw.
- **Benefit:** Escapes such as `json.__spec__.loader.get_data(path)` (arbitrary file read) or walking a traceback's deep frame to the real builtins are blocked, closing the gap between what the denylist knew about modules and what introspection could actually reach.

#### Fixed: Failed keychain reads no longer replaced the stored key
- **Files:** `src/equinox/security/keystore.py`, `src/equinox/security/crypto.py`, `tests/security/test_keystore_failure_modes.py`
- **Change:** When the OS key store is enabled but unreadable, `get_from_os_store()`/`get_or_create_os_key()` now raise `KeystoreUnavailableError` instead of generating a fresh key on any failure. Only "no backend at all" (or the feature disabled) still falls back to the file key.
- **Benefit:** A transient keychain error (locked store, D-Bus hiccup) can no longer silently overwrite the stored key and orphan everything encrypted under it. A corrupt stored value is likewise raised, not treated as "no key".

#### Fixed: Master-password precedence and loader failures
- **Files:** `src/equinox/security/auth_cipher.py`, `tests/security/test_auth_cipher_key_precedence.py`
- **Change:** A file-key Fernet cached early in startup no longer shadows a provided `master_password_loader`; a cached master-password Fernet always wins, and a loader that *raises* now surfaces a `SecurityError` instead of being swallowed into a silent file-key fallback (only a loader returning `None` means "unconfigured").
- **Benefit:** Auth data is encrypted under the master password when a master password is configured, and a broken keyring or cancelled prompt can no longer encrypt blobs under the file key without the user knowing.

#### Fixed: Secret rotation honored the cached password over the explicit one
- **Files:** `src/equinox/security/secrets_password.py`, `tests/security/test_secret_rotation.py`
- **Change:** `get_fernet_for_password()` returns the cached instance only when no password was supplied; an explicit password re-derives and replaces the cache. The derived-salt file is now created with `O_EXCL` and `0o600` so a concurrent process cannot clobber it and a shared host cannot read it.
- **Benefit:** `equinox rotate-secrets --new-password` re-encrypts under the *new* key instead of silently reporting success while re-encrypting under the old one — the case that mattered most when rotating a leaked credential.

#### Updated: Display sanitization split out of `Validator`
- **Files:** `src/equinox/core/validation/__init__.py`, call sites in the client and test suites
- **Change:** Display-text truncation/control-character stripping lives in `Sanitizer.sanitize_for_display()`; `Validator.sanitize_for_display()` no longer exists. Validation (zero-trust, all untrusted inputs) and display formatting are now separate APIs.
- **Benefit:** Makes it explicit that sanitizing strings for logs/UI does not substitute for validating untrusted data at a boundary.

### GUI & Client

#### Fixed: Pooled HTTP clients left open after worker sends
- **Files:** `src/equinox/core/client/http_client.py`, `src/equinox/gui/workers.py`
- **Change:** Added `HTTPClient.close()` and made `RequestWorker`/`BenchmarkWorker` close the client in a `finally` block instead of leaving the connection pool to the garbage collector.
- **Benefit:** Per-send and benchmark clients release their sockets deterministically, avoiding stranded connections under repeated sends.

#### Fixed: Preparation issues dropped their field name
- **Files:** `src/equinox/application/requests/preparation.py`
- **Change:** Multi-field validation failures are prefixed with the offending field name when rendering issue messages.
- **Benefit:** A request that fails several checks now reports which check failed, instead of a list of ambiguous sentences.

### Correctness

#### Fixed: Datetime handling and URL parsing method
- **Files:** `src/equinox/core/`
- **Change:** datetime usage aligned with the `datetime.now(timezone.utc)` convention and the URL parsing call updated to the current `urlps` method.
- **Benefit:** Removes deprecation-prone behavior and keeps the URL parser on the supported integration path.

### Dependencies & Tooling

#### Updated: `urlps` pin relaxed
- **Files:** `pyproject.toml`
- **Change:** `urlps==0.7.0` → `urlps>=0.7.0` so 0.7.x patch releases install normally.
- **Benefit:** Dependency updates no longer require manifest churn for compatible releases.

#### Updated: `keyring` treated as an optional import under mypy strict
- **Files:** `pyproject.toml`, `src/equinox/security/keystore.py`
- **Change:** The `keyring.*` module override replaces an inline `# type: ignore[import-not-found]` that went unused whenever keyring was installed (and would have broken any environment without it).
- **Benefit:** Strict type-checking passes the same way whether or not the optional keyring dependency is present.

#### Added: Explicit waivers for unpatched advisories in the CVE gate
- **Files:** `scripts/check_dependency_vulnerabilities.py`, `tests/scripts/test_check_dependency_vulnerabilities.py`
- **Change:** The dependency scan now supports a minimal, documented waiver map. A waiver applies only to advisories with no published upstream fix ("fixed in: none listed"), each entry must carry its justification, and every waived advisory is printed to the scan output. Once upstream ships a fix, the entry stops applying and the scan fails until the dependency is upgraded.
- **Benefit:** PYSEC-2026-3740 (nltk model import/export sandbox bypass, unpatched upstream and unreachable from Equinox, which never imports nltk) no longer wedges the release gate, without weakening the scan: everything with an available fix still blocks.

#### Updated: `AGENTS.md` rewritten as an agent-facing repo guide
- **Files:** `AGENTS.md`
- **Change:** Replaced stale claims (outdated coverage floor, Python matrix) with the verified quality gate, documented pre-commit-vs-CI divergences (the swallowing `ruff --fix --exit-zero` hook, `detect-secrets` baseline policy), and called out high-mistake areas such as the dirty/autosave invariant and history reconstruction.
- **Benefit:** Agents working in the repo ramp up from verified facts instead of trial and error.

---

## Upgrade Notes

- **`Validator.sanitize_for_display()` was removed.** Call `Sanitizer.sanitize_for_display()` instead (same `equinox.core.validation` package, same signature). Searching your code for `sanitize_for_display` will find every affected call site.
- **`Validator.validate_resolved_url()` gained a keyword-only `tolerate_dns_failure=False` parameter.** Existing calls are unaffected; only code that deliberately wants DNS-failure tolerance (the Vault insecure-http path) should pass it.
- **Vault insecure-http mode is stricter.** It no longer bypasses the SSRF guard for arbitrary hosts: loopback (`http://127.0.0.1:8200`) and unresolvable hostnames still work, but metadata hostnames and literal private addresses are rejected as before. If a previously working local Vault URL now fails, it is failing for the reason this release exists.
- No database migrations and no changes to stored data. Existing databases open in place; `enc:`-prefixed secrets continue to decrypt with the same master password or file key.
