-- Equinox Database Schema

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
    headers TEXT,  -- JSON
    params TEXT,   -- JSON
    body TEXT,
    auth_type TEXT,
    auth_data TEXT,  -- JSON
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
    request_headers TEXT,  -- JSON
    request_body TEXT,
    response_headers TEXT,  -- JSON
    response_body BLOB,
    elapsed REAL,  -- Response time in seconds
    error TEXT,
    executed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (request_id) REFERENCES requests(id) ON DELETE SET NULL
);

-- Environments table
CREATE TABLE IF NOT EXISTS environments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    variables TEXT NOT NULL,  -- JSON
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

-- Indexes for better performance
CREATE INDEX IF NOT EXISTS idx_requests_collection ON requests(collection_id);
CREATE INDEX IF NOT EXISTS idx_history_request ON history(request_id);
CREATE INDEX IF NOT EXISTS idx_history_executed_at ON history(executed_at DESC);
CREATE INDEX IF NOT EXISTS idx_environments_active ON environments(is_active);
CREATE INDEX IF NOT EXISTS idx_collection_variables_collection ON collection_variables(collection_id);
CREATE INDEX IF NOT EXISTS idx_variable_group_items_group ON variable_group_items(group_id);
CREATE INDEX IF NOT EXISTS idx_collection_variable_groups_collection ON collection_variable_groups(collection_id);
CREATE INDEX IF NOT EXISTS idx_collection_variable_groups_group ON collection_variable_groups(group_id);
