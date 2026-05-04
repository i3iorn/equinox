from enum import Enum


class AuditEventType(Enum):
    """Types of audit events."""

    # Authentication
    AUTH_SUCCESS = "auth_success"
    AUTH_FAILURE = "auth_failure"
    AUTH_TOKEN_REFRESH = "auth_token_refresh"

    # Credentials
    CREDENTIAL_STORED = "credential_stored"
    CREDENTIAL_RETRIEVED = "credential_retrieved"
    CREDENTIAL_DELETED = "credential_deleted"
    CREDENTIAL_EXPORT = "credential_export"

    # HTTP Requests
    REQUEST_SENT = "request_sent"
    REQUEST_FAILED = "request_failed"

    # File Operations
    FILE_READ = "file_read"
    FILE_WRITE = "file_write"
    FILE_DELETE = "file_delete"

    # Plugin System
    PLUGIN_LOADED = "plugin_loaded"
    PLUGIN_UNLOADED = "plugin_unloaded"
    PLUGIN_ERROR = "plugin_error"

    # Security Events
    VALIDATION_FAILURE = "validation_failure"
    RATE_LIMIT_EXCEEDED = "rate_limit_exceeded"
    SSL_VERIFICATION_FAILED = "ssl_verification_failed"
    INJECTION_ATTEMPT = "injection_attempt"

    # Configuration
    CONFIG_CHANGED = "config_changed"
    SETTINGS_UPDATED = "settings_updated"
