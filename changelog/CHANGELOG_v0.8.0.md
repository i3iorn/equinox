# Changelog — Equinox v0.8.0

**Release Date:** November 3, 2026

## Overview

Equinox v0.8.0 is a security-hardening release built around the URL handling
library **urlps**. The hand-rolled URL parsing, SSRF guard, and URL-redaction
regexes (~370 lines) have been replaced by delegation to `urlps >= 2.1.0`,
which now covers the gaps identified upstream (i3iorn/urlps issues #74 and
#78). This also folds in the previously untagged v0.7.1–v0.7.3 UX work
(see `changelog/CHANGELOG_v0.7.1.md` through `CHANGELOG_v0.7.3.md`).

No breaking changes for stored data: requests, collections, credentials,
history, and cookies load and save exactly as before. No database migrations.

---

## [0.8.0] — 2026-11-03

### Security Improvements

#### urlps offload — validation (`core/validation/_url.py`)
- Structural URL validation now goes through `urlps.parse_url_local` instead
  of `urlparse`-based part inspection; `validate_resolved()` runs
  `urlps.resolve_and_validate` (DNS resolution, SSRF risk, cloud metadata
  endpoints, private/loopback/link-local literals).
- The `EQUINOX_SSRF_ALLOW_ON_DNS_FAILURE` flag (and the `tolerate_dns_failure`
  option) now maps to a strict-parse-without-DNS path: literal private IPs and
  metadata hosts stay blocked offline; only the DNS step is relaxed.
- Deleted the internal `_SsrfGuard`/DNS-pool module (`core/validation/_ssrf.py`)
  — urlps' DNS cache, rate limiter, and retry policy replace it.
- `Validator.validate_hostname()`/`_check_ssrf()` delegate through the same
  urlps policy path as full URLs.

#### Injection-payload detection via urlps `SecurityFindings` (urlps #78)
- `Validator.validate_url()` now consumes urlps' findings on the *decoded*
  path/query (`script_payload_in_path`, `inline_event_handler`), catching
  percent-encoded variants like `%3Cscript%3E` the old regexes missed.
- The raw-string XSS regex scan is retained as a complement: urlps does not
  flag script tags inside query strings, and template (`{{placeholder}}`)
  URLs are unparseable at import time.

#### URL redaction now fully offloaded (`security/redactor.py`)
- `redact_url()` calls `urlps.redact_url_for_logs` (urlps #74): redaction
  works on the raw string, so URLs that fail host grammar — e.g.
  `https://{{BASE_URL}}/api?token=supersecret` — are masked instead of
  leaking secrets through log/error fallback paths.
- URLs with nothing to mask are returned byte-identical (no canonicalization
  side effects in proxies/error details/UI).
- `bearer` is passed as an `extra_sensitive_keys` entry — the one sensitive
  parameter key urlps' built-in set does not cover.
- JWT-shaped values under non-sensitive keys keep local masking
  (`mask_jwt_shaped`, issue #69); urlps' `%2A%2A%2A` mask is translated to
  Equinox's standard `[REDACTED]` token for consistent display in history,
  exports, and logs.

### Breaking Change (dependency only)

- **`urlps>=1.2.0` → `urlps>=2.1.0`.** v0.8.0 uses APIs introduced in urlps
  2.1.0 (`redact_url_for_logs`, `SecurityPolicy.flag_injection_payloads`).
- Error wording for blocked hosts changed ("Host poses SSRF risk and is
  disallowed" instead of "private network") — code matching on the old
  message should match on `SSRF|security policy` or the `ValidationError`.

### Internal

- ~370 lines of hand-rolled validation/redaction removed; the `Validator`
  facade stays as a thin delegate over urlps.
- Tests updated to urlps' masking contracts (username retained in
  `user:***@`-style credentials; password masked).

---

## Compatibility

- No API or data-format changes. Existing collections, credentials, history
  databases, and stored cookies load and save exactly as before.
- Minimum Python version remains 3.10; PyQt6>=6.5.0,<7.0.0. urlps>=2.1.0 is
  a new floor for the `extras`-installed dependency set.
