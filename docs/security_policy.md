Security Policy: Master Passwords & Rotation

Overview
- Equinox supports encryption-at-rest for secrets using a master password derived key (PBKDF2 + Fernet).
- Secrets include OAuth2 client secrets, saved credentials, and per-collection auth data.

Master Password & Startup Rotation
- A global master password can be configured via EQUINOX_MASTER_PASSWORD (env) or interactively at startup when not provided.
- On startup (bootstrap), if EQUINOX_DB_PATH is provided, plaintext secrets are rotated to enc: blobs using the master password. This ensures secrets are encrypted even if they were created before the master-password feature existed.
- Salt is stored at ~/.equinox/salt.bin to derive a stable key across restarts.

Rotation Semantics
- rotate_all_secrets(db_path, new_password) will re-encrypt plaintext secrets with a Fernet derived from new_password.
- If a value already begins with the enc: prefix, it is skipped (no decryption attempt of prior blobs).
- Encrypted blobs (enc:) are not decrypted with a newer key; their ciphertext remains opaque and is preserved unless a separate rotation path is introduced.
- A startup rotation can be forced by setting EQUINOX_DB_PATH; a separate CLI command (rotate-secrets) drives rotation explicitly with a provided new password.

CLI & Automation
- CLI subcommand: equinox rotate-secrets --db-path <path> --new-password <password>
- If --new-password is omitted, the command prompts for it securely instead of accepting it as a plaintext argument.
- In production setups, consider an automation hook that rotates secrets after a key rotation event, or periodically as part of maintenance.

Threat Model & Remediation
- Key rotation: plan a policy for rotating the master password. This library supports rotating plaintext secrets; properly encrypted data remains protected even if the old key is compromised unless the old key is also leaked in plaintext.
- Do not log raw secrets. All rotation behavior redacts tokens when shown in logs.
- Ensure backups are protected since plaintext secrets may be present prior to rotation in backups.

Operational Notes
- Tests have been added to verify rotation behavior and that extra_params flow through OAuth2 tokens.
- Documentation has been updated to reflect the new startup rotation flow and the rotate-secrets CLI.
