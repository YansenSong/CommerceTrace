PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS ecommerce.customers (
    customer_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    region TEXT NOT NULL,
    registered_at TEXT NOT NULL,
    acquisition_channel TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ecommerce.categories (
    category_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS ecommerce.products (
    product_id INTEGER PRIMARY KEY,
    category_id INTEGER NOT NULL REFERENCES categories(category_id),
    name TEXT NOT NULL,
    current_price REAL NOT NULL CHECK (current_price >= 0),
    current_cost REAL NOT NULL CHECK (current_cost >= 0)
);

CREATE TABLE IF NOT EXISTS ecommerce.orders (
    order_id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(customer_id),
    ordered_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('paid', 'completed', 'cancelled', 'refunded')),
    channel TEXT NOT NULL,
    total_amount REAL NOT NULL CHECK (total_amount >= 0)
);

CREATE TABLE IF NOT EXISTS ecommerce.order_items (
    order_item_id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(order_id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL REFERENCES products(product_id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    unit_price REAL NOT NULL CHECK (unit_price >= 0),
    unit_cost REAL NOT NULL CHECK (unit_cost >= 0)
);

CREATE TABLE IF NOT EXISTS ecommerce.payments (
    payment_id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL UNIQUE REFERENCES orders(order_id) ON DELETE CASCADE,
    paid_at TEXT NOT NULL,
    payment_method TEXT NOT NULL,
    amount REAL NOT NULL CHECK (amount >= 0)
);

CREATE TABLE IF NOT EXISTS ecommerce.refunds (
    refund_id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(order_id) ON DELETE CASCADE,
    refunded_at TEXT NOT NULL,
    reason TEXT NOT NULL,
    amount REAL NOT NULL CHECK (amount >= 0)
);

CREATE TABLE IF NOT EXISTS ecommerce.inventory_snapshots (
    snapshot_date TEXT NOT NULL,
    product_id INTEGER NOT NULL REFERENCES products(product_id),
    stock_quantity INTEGER NOT NULL CHECK (stock_quantity >= 0),
    PRIMARY KEY (snapshot_date, product_id)
);

CREATE TABLE IF NOT EXISTS agent_app.anonymous_users (
    user_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS agent_app.dataset_metadata (
    singleton INTEGER PRIMARY KEY DEFAULT 1 CHECK (singleton = 1),
    data_version TEXT NOT NULL,
    seed INTEGER NOT NULL,
    profile TEXT NOT NULL,
    result_hash TEXT NOT NULL,
    row_counts TEXT NOT NULL,
    generated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS agent_app.conversations (
    conversation_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES anonymous_users(user_id),
    title TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS agent_app.conversations_user_updated_idx
    ON conversations (user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS agent_app.messages (
    message_id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'tool')),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS agent_app.stream_events (
    event_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
    request_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS agent_app.stream_events_conversation_idx
    ON stream_events (conversation_id, created_at, event_id);

CREATE TABLE IF NOT EXISTS agent_app.tool_calls (
    tool_call_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
    request_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    arguments TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS agent_app.tool_results (
    tool_result_id INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_call_id TEXT NOT NULL REFERENCES tool_calls(tool_call_id) ON DELETE CASCADE,
    success INTEGER NOT NULL,
    result_summary TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS agent_app.evidence (
    evidence_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
    request_id TEXT NOT NULL,
    analysis_step TEXT NOT NULL,
    tool_call_id TEXT NOT NULL,
    claim TEXT NOT NULL,
    sql TEXT NOT NULL,
    columns_json TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    result_hash TEXT NOT NULL,
    execution_time_ms REAL NOT NULL DEFAULT 0,
    preview_json TEXT NOT NULL,
    executed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_app.charts (
    chart_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
    request_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL REFERENCES evidence(evidence_id) ON DELETE CASCADE,
    chart_type TEXT NOT NULL,
    title TEXT NOT NULL,
    figure_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS agent_app.memory_records (
    memory_id TEXT PRIMARY KEY,
    dedupe_key TEXT NOT NULL UNIQUE,
    question TEXT NOT NULL,
    analysis_step TEXT NOT NULL,
    normalized_sql TEXT NOT NULL,
    tables_and_columns TEXT NOT NULL,
    schema_fingerprint TEXT NOT NULL,
    metric_versions TEXT NOT NULL,
    execution_time_ms REAL NOT NULL DEFAULT 0,
    row_count INTEGER NOT NULL DEFAULT 0,
    column_names TEXT NOT NULL,
    limited_summary TEXT NOT NULL,
    result_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('candidate', 'trusted', 'stale', 'rejected')),
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_verified_at TEXT
);
CREATE INDEX IF NOT EXISTS agent_app.memory_records_status_idx
    ON memory_records (status, created_at DESC);
