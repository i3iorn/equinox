-- Equinox Database Schema
-- This file is the canonical reference for the full schema.
-- At runtime, the migration system (migrations.py) manages the actual schema.

-- Collections table
CREATE TABLE IF NOT EXISTS collections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Requests table
CREATE TABLE IF NOT EXISTS requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    collection_id INTEGER,
    name TEXT NOT NULL,
    description TEXT,
    method TEXT NOT NULL,
    url TEXT NOT NULL,
    headers TEXT,              -- JSON
    params TEXT,               -- JSON
    body TEXT,
    auth_type TEXT,
    auth_data TEXT,            -- JSON
    tags TEXT DEFAULT '',                   -- (v2) comma-separated
    folder TEXT DEFAULT '',                 -- (v2) logical grouping
    timeout REAL DEFAULT 30.0,             -- (v3) seconds
    verify_ssl INTEGER DEFAULT 1,          -- (v3) boolean
    follow_redirects INTEGER DEFAULT 1,    -- (v4) boolean
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (collection_id) REFERENCES collections(id) ON DELETE CASCADE
);

-- History table (execution history)
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id INTEGER,
    method TEXT NOT NULL,
    url TEXT NOT NULL,
    status_code INTEGER,
    reason TEXT,
    request_headers TEXT,      -- JSON
    request_body TEXT,
    response_headers TEXT,     -- JSON
    response_body BLOB,
    elapsed REAL,              -- Response time in seconds
    error TEXT,
    response_size INTEGER,                 -- (v4) bytes
    environment_id INTEGER,                -- (v5) context tracking
    executed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (request_id) REFERENCES requests(id) ON DELETE SET NULL,
    FOREIGN KEY (environment_id) REFERENCES environments(id) ON DELETE SET NULL
);

-- Environments table
CREATE TABLE IF NOT EXISTS environments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    variables TEXT NOT NULL,   -- JSON
    is_active INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Collection variables table (variables specific to a collection)
CREATE TABLE IF NOT EXISTS collection_variables (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    collection_id INTEGER NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (collection_id) REFERENCES collections(id) ON DELETE CASCADE,
    UNIQUE(collection_id, key)
);

-- Variable groups table (reusable sets of variables)
CREATE TABLE IF NOT EXISTS variable_groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Variable group items table (key-value pairs in a group)
CREATE TABLE IF NOT EXISTS variable_group_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (group_id) REFERENCES variable_groups(id) ON DELETE CASCADE,
    UNIQUE(group_id, key)
);

-- Collection variable groups table (many-to-many relationship)
CREATE TABLE IF NOT EXISTS collection_variable_groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    collection_id INTEGER NOT NULL,
    group_id INTEGER NOT NULL,
    priority INTEGER DEFAULT 0,  -- Lower number = higher priority
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (collection_id) REFERENCES collections(id) ON DELETE CASCADE,
    FOREIGN KEY (group_id) REFERENCES variable_groups(id) ON DELETE CASCADE,
    UNIQUE(collection_id, group_id)
);

-- OAuth2 clients table (v7) — named, reusable OAuth2 credentials
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

-- Saved credentials table (v8) — reusable auth configs of any type
CREATE TABLE IF NOT EXISTS saved_credentials (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE,
    auth_type   TEXT    NOT NULL,       -- oauth2 | api_key | basic | bearer
    config      TEXT    NOT NULL DEFAULT '{}',  -- JSON blob
    description TEXT    DEFAULT '',
    is_default  INTEGER DEFAULT 0,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for better performance
CREATE INDEX IF NOT EXISTS idx_requests_collection ON requests(collection_id);
CREATE INDEX IF NOT EXISTS idx_history_request ON history(request_id);
CREATE INDEX IF NOT EXISTS idx_history_executed_at ON history(executed_at DESC);
CREATE INDEX IF NOT EXISTS idx_history_url ON history(url);
CREATE INDEX IF NOT EXISTS idx_environments_active ON environments(is_active);
CREATE INDEX IF NOT EXISTS idx_collection_variables_collection ON collection_variables(collection_id);
CREATE INDEX IF NOT EXISTS idx_variable_group_items_group ON variable_group_items(group_id);
CREATE INDEX IF NOT EXISTS idx_collection_variable_groups_collection ON collection_variable_groups(collection_id);
CREATE INDEX IF NOT EXISTS idx_collection_variable_groups_group ON collection_variable_groups(group_id);
CREATE INDEX IF NOT EXISTS idx_oauth_clients_default ON oauth_clients(is_default);
CREATE INDEX IF NOT EXISTS idx_saved_creds_type ON saved_credentials(auth_type);
CREATE INDEX IF NOT EXISTS idx_saved_creds_default ON saved_credentials(is_default);
