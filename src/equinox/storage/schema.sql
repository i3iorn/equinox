-- Equinox Database Schema (reference only — not used at runtime)
-- The migration system (migrations.py) manages the actual schema.
-- Last updated: migration v21

-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    description TEXT    NOT NULL,
    applied_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Collections table
CREATE TABLE IF NOT EXISTS collections (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE,
    description TEXT,
    auth_type   TEXT,                              -- (v15) hierarchical auth
    auth_data   TEXT,                              -- (v15) encrypted JSON
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Requests table
CREATE TABLE IF NOT EXISTS requests (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    collection_id     INTEGER,
    name              TEXT    NOT NULL,
    description       TEXT,
    method            TEXT    NOT NULL,
    url               TEXT    NOT NULL,
    headers           TEXT,                        -- JSON
    params            TEXT,                        -- JSON (list or dict)
    body              TEXT,
    auth_type         TEXT,
    auth_data         TEXT,                        -- encrypted JSON
    tags              TEXT    DEFAULT '',           -- (v2) comma-separated
    folder            TEXT    DEFAULT '',           -- (v2) logical grouping
    timeout           REAL    DEFAULT 30.0,        -- (v3) seconds
    verify_ssl        INTEGER DEFAULT 1,           -- (v3) boolean
    follow_redirects  INTEGER DEFAULT 1,           -- (v4) boolean
    captures          TEXT    DEFAULT '[]',         -- (v9) JSON array
    pre_script        TEXT    DEFAULT '',           -- (v10)
    post_script       TEXT    DEFAULT '',           -- (v10)
    cert_path         TEXT,                        -- (v10)
    cert_key_path     TEXT,                        -- (v10)
    sort_order        INTEGER DEFAULT 0,           -- (v14) manual ordering
    assertions        TEXT    DEFAULT '[]',         -- (v17) JSON array
    path_params       TEXT    DEFAULT '{}',         -- (v18) JSON dict
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (collection_id) REFERENCES collections(id) ON DELETE CASCADE
);

-- History table (execution history)
CREATE TABLE IF NOT EXISTS history (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id       INTEGER,
    method           TEXT NOT NULL,
    url              TEXT NOT NULL,
    status_code      INTEGER,
    reason           TEXT,
    request_headers  TEXT,                         -- JSON
    request_body     TEXT,
    response_headers TEXT,                         -- JSON
    response_body    BLOB,
    elapsed          REAL,                         -- seconds
    error            TEXT,
    response_size    INTEGER,                      -- (v4) bytes
    environment_id   INTEGER,                      -- (v5) context tracking
    executed_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (request_id) REFERENCES requests(id) ON DELETE SET NULL,
    FOREIGN KEY (environment_id) REFERENCES environments(id) ON DELETE SET NULL
);

-- Environments table
CREATE TABLE IF NOT EXISTS environments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE,
    description TEXT,
    variables   TEXT    NOT NULL,                   -- JSON
    is_active   INTEGER DEFAULT 0,
    secret_keys TEXT    DEFAULT '[]',               -- (v16) JSON array
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Collection variables table
CREATE TABLE IF NOT EXISTS collection_variables (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    collection_id INTEGER NOT NULL,
    key           TEXT    NOT NULL,
    value         TEXT    NOT NULL,
    description   TEXT,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (collection_id) REFERENCES collections(id) ON DELETE CASCADE,
    UNIQUE(collection_id, key)
);

-- Variable groups table
CREATE TABLE IF NOT EXISTS variable_groups (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE,
    description TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Variable group items table
CREATE TABLE IF NOT EXISTS variable_group_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id    INTEGER NOT NULL,
    key         TEXT    NOT NULL,
    value       TEXT    NOT NULL,
    description TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (group_id) REFERENCES variable_groups(id) ON DELETE CASCADE,
    UNIQUE(group_id, key)
);

-- Collection ↔ variable-group many-to-many
CREATE TABLE IF NOT EXISTS collection_variable_groups (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    collection_id INTEGER NOT NULL,
    group_id      INTEGER NOT NULL,
    priority      INTEGER DEFAULT 0,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (collection_id) REFERENCES collections(id) ON DELETE CASCADE,
    FOREIGN KEY (group_id) REFERENCES variable_groups(id) ON DELETE CASCADE,
    UNIQUE(collection_id, group_id)
);

-- OAuth2 clients table (v7)
CREATE TABLE IF NOT EXISTS oauth_clients (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT    NOT NULL UNIQUE,
    token_url     TEXT    NOT NULL,
    client_id     TEXT    NOT NULL,
    client_secret TEXT    NOT NULL DEFAULT '',
    scope         TEXT    DEFAULT '',
    grant_type    TEXT    NOT NULL DEFAULT 'client_credentials',
    extra_params  TEXT    DEFAULT '{}',
    description   TEXT    DEFAULT '',
    is_default    INTEGER DEFAULT 0,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Saved credentials table (v8)
CREATE TABLE IF NOT EXISTS saved_credentials (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE,
    auth_type   TEXT    NOT NULL,
    config      TEXT    NOT NULL DEFAULT '{}',      -- encrypted JSON
    description TEXT    DEFAULT '',
    is_default  INTEGER DEFAULT 0,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Cookies table (v11)
CREATE TABLE IF NOT EXISTS cookies (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    value      TEXT NOT NULL DEFAULT '',
    domain     TEXT NOT NULL DEFAULT '',
    path       TEXT NOT NULL DEFAULT '/',
    secure     INTEGER DEFAULT 0,
    http_only  INTEGER DEFAULT 0,
    expires    TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(name, domain, path)
);

-- Collection folders table (v13)
CREATE TABLE IF NOT EXISTS collection_folders (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    path          TEXT NOT NULL,
    description   TEXT DEFAULT '',
    auth_type     TEXT,                            -- (v15)
    auth_data     TEXT,                            -- (v15)
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(collection_id, path)
);

-- Performance indexes
CREATE INDEX IF NOT EXISTS idx_requests_collection ON requests(collection_id);
CREATE INDEX IF NOT EXISTS idx_history_executed_at ON history(executed_at DESC);
CREATE INDEX IF NOT EXISTS idx_history_url ON history(url);
CREATE INDEX IF NOT EXISTS idx_history_status_code ON history(status_code);
CREATE INDEX IF NOT EXISTS idx_history_method ON history(method);
CREATE INDEX IF NOT EXISTS idx_environments_active ON environments(is_active);
CREATE INDEX IF NOT EXISTS idx_collection_variables_collection ON collection_variables(collection_id);
CREATE INDEX IF NOT EXISTS idx_variable_group_items_group ON variable_group_items(group_id);
CREATE INDEX IF NOT EXISTS idx_collection_variable_groups_collection ON collection_variable_groups(collection_id);
CREATE INDEX IF NOT EXISTS idx_collection_variable_groups_group ON collection_variable_groups(group_id);
CREATE INDEX IF NOT EXISTS idx_oauth_clients_default ON oauth_clients(is_default);
CREATE INDEX IF NOT EXISTS idx_saved_creds_type ON saved_credentials(auth_type);
CREATE INDEX IF NOT EXISTS idx_saved_creds_default ON saved_credentials(is_default);
CREATE INDEX IF NOT EXISTS idx_collection_folders_collection ON collection_folders(collection_id);

-- Response Intelligence tables (v20)
CREATE TABLE IF NOT EXISTS endpoint_stats (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    url_pattern     TEXT    NOT NULL,
    method          TEXT    NOT NULL,
    call_count      INTEGER DEFAULT 0,
    total_elapsed   REAL    DEFAULT 0,
    min_elapsed     REAL,
    max_elapsed     REAL,
    elapsed_values  TEXT    DEFAULT '[]',
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_endpoint_stats_pattern
    ON endpoint_stats(url_pattern, method);

CREATE TABLE IF NOT EXISTS response_schemas (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    url_pattern     TEXT    NOT NULL,
    method          TEXT    NOT NULL,
    schema_hash     TEXT    NOT NULL,
    schema_json     TEXT    NOT NULL,
    captured_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_response_schemas_pattern
    ON response_schemas(url_pattern, method);

-- History index table for normalized URL indexing (v21)
CREATE TABLE IF NOT EXISTS history_index (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    history_id       INTEGER UNIQUE NOT NULL,
    method           TEXT    NOT NULL,
    normalized_url   TEXT    NOT NULL,
    path_segments    TEXT    NOT NULL,
    query_params     TEXT    NOT NULL,
    body_hash        TEXT,
    response_success INTEGER NOT NULL DEFAULT 0,
    executed_at      TEXT,
    FOREIGN KEY (history_id) REFERENCES history(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_history_index_method_norm
    ON history_index(method, normalized_url);
