# Changelog — Equinox v0.7.2

**Release Date:** October 26, 2026

## Overview

Equinox v0.7.2 is a security- and performance-focused release. It hardens the
handling of secrets across the entire stack — Postman export, stored cookies,
the keyring, auth token flows, and redirect handling — so that credentials can
no longer leak into logs, storage, or exported files, and corrupted or
malformed secret data now fails loudly instead of silently degrading. On the
performance side, response bodies are decoded lazily, history storage avoids
redundant work, and the cookie jar is rebuilt and saved only when it actually
changes.

No breaking changes: existing requests, collections, credentials, and history
databases load and save exactly as before.

---

## [0.7.2] — 2026-10-26

### Security

- **Postman export redaction** (#61, #83): Exported collections no longer leak
  secrets via raw URLs. Query parameters such as `?api_key=...` are redacted in
  `_PreparedRequest.from_row` alongside headers and body fields.
- **OAuth 2.0 snapshot redaction** (#69, #91): Access/refresh tokens, including
  raw token-response fallback values, are masked in the auth snapshot rather
  than stored as plaintext.
- **Stored cookie encryption at rest** (#65, #87): Cookies in the SQLite store
  are encrypted with the keyring cipher; legacy plaintext rows are detected and
  upgraded on read. This is supported by the `enc:` prefix collision fix (#67,
  #89) — legacy plaintext values that happen to start with `enc:` now read back
  with a warning instead of raising, while token-shaped corruption still fails
  loudly.
- **Keyring / keystore hardening** (#62, #63, #68, #88, #92):
  - The Fernet cipher cache is invalidated on key rotation so stale keys are
    never reused.
  - A failed keyring write no longer leaves orphaned ciphertext behind.
  - Stored keys are validated strictly: base64 decoding uses `validate=True`,
    the decoded key must be exactly 32 bytes, and all-zero keys are rejected.
  - A missing or degraded key file now logs a WARNING instead of silently
    falling back.
  - The legacy files-based keyring branch validates paths and rejects traversal
    attempts.
- **Keyring salt integrity** (#71, #93): A 16-byte salt is enforced (failing
  loudly on corrupt `salt.bin`), environment-variable master passwords are
  announced with a WARNING, and the string-wipe limitation of the cipher
  backend is documented.
- **Redirect credential containment** (#64, #86): Authorization headers and
  credentials are stripped on cross-origin redirects and covered by
  end-to-end redirect tests.
- **Cookie encryption failures** are surfaced instead of silently degrading to
  plaintext storage.

### Performance

- **Lazy decoding in the history layer**: Response bodies are decoded only when
  actually needed, and streaming post-filtering skips header JSON parsing for
  rejected rows; the body probe uses a bounded substring scan instead of a
  full-body scan (#73, #95).
- **History projection**: History queries use a body-free projection instead of
  loading full BLOBs with `SELECT *` (#72, #94).
- **Streaming lazy decode / JSON pretty-print**: Large response bodies are
  decoded lazily and pretty-printing is streamed rather than building the whole
  string in memory.
- **Request body filtering / size gate** (#76, #98): Large bodies are gated
  before `json.dumps` is run on the GUI thread, with size-gate tests pinning
  the behavior.
- **Cookie jar optimization** (#74, #75, #96): The cookie jar is rebuilt only
  when the underlying store changes, and saves are skipped entirely when
  nothing changed — the previous per-save `COUNT(*)` and split writes are gone.
- **Reduced per-request overhead** (#77, #99): Remaining hot-path log extras
  (e.g. `redact_url`) are guarded behind `isEnabledFor` so no formatting work
  happens on disabled log levels.

### Bug Fixes / Core

- **Dispatcher validation fixes**: TLS metadata and dispatcher validation edge
  cases are now covered by tests.
- **History projection and parsing fixes**: History projection, index, and
  search/parser edge cases were tightened.
- **Timeout retry integration** (#50, #80): Timeout retries are now covered by
  integration tests to guarantee they engage.
- **Suppressed request errors** (#51, #81): A domain error surfaced through
  `SUPPRESS` is pinned to `SuppressedRequestError`.
- **Multipart requests** (#49): Silent empty multipart bodies were fixed.
- **Placeholder expansion** (#52, #82): Failures are no longer swallowed;
  warnings are deduplicated and redacted.
- Numerous test-suite additions and hardening across auth, client, security,
  storage, and GUI layers.

---

## Compatibility

- No API or data-format changes. Existing collections, credentials, history
  databases, and stored cookies load and save exactly as before; legacy
  plaintext cookie rows are transparently upgraded.
- The keyring now rejects corrupt stored keys (`salt.bin`, non-base64,
  wrong-length, or all-zero keys) instead of silently degrading — affected
  users will be prompted to re-enter their master password rather than see
  opaque decryption failures.
