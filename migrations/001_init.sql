CREATE SCHEMA IF NOT EXISTS ecommerce;
CREATE SCHEMA IF NOT EXISTS agent_app;

CREATE TABLE IF NOT EXISTS ecommerce.customers (
    customer_id BIGINT PRIMARY KEY,
    name TEXT NOT NULL,
    region TEXT NOT NULL,
    registered_at TIMESTAMPTZ NOT NULL,
    acquisition_channel TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ecommerce.categories (
    category_id BIGINT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS ecommerce.products (
    product_id BIGINT PRIMARY KEY,
    category_id BIGINT NOT NULL REFERENCES ecommerce.categories(category_id),
    name TEXT NOT NULL,
    current_price NUMERIC(14, 2) NOT NULL CHECK (current_price >= 0),
    current_cost NUMERIC(14, 2) NOT NULL CHECK (current_cost >= 0)
);

CREATE TABLE IF NOT EXISTS ecommerce.orders (
    order_id BIGINT PRIMARY KEY,
    customer_id BIGINT NOT NULL REFERENCES ecommerce.customers(customer_id),
    ordered_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('paid', 'completed', 'cancelled', 'refunded')),
    channel TEXT NOT NULL,
    total_amount NUMERIC(14, 2) NOT NULL CHECK (total_amount >= 0)
);

CREATE TABLE IF NOT EXISTS ecommerce.order_items (
    order_item_id BIGINT PRIMARY KEY,
    order_id BIGINT NOT NULL REFERENCES ecommerce.orders(order_id) ON DELETE CASCADE,
    product_id BIGINT NOT NULL REFERENCES ecommerce.products(product_id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    unit_price NUMERIC(14, 2) NOT NULL CHECK (unit_price >= 0),
    unit_cost NUMERIC(14, 2) NOT NULL CHECK (unit_cost >= 0)
);

CREATE TABLE IF NOT EXISTS ecommerce.payments (
    payment_id BIGINT PRIMARY KEY,
    order_id BIGINT NOT NULL UNIQUE REFERENCES ecommerce.orders(order_id) ON DELETE CASCADE,
    paid_at TIMESTAMPTZ NOT NULL,
    payment_method TEXT NOT NULL,
    amount NUMERIC(14, 2) NOT NULL CHECK (amount >= 0)
);

CREATE TABLE IF NOT EXISTS ecommerce.refunds (
    refund_id BIGINT PRIMARY KEY,
    order_id BIGINT NOT NULL REFERENCES ecommerce.orders(order_id) ON DELETE CASCADE,
    refunded_at TIMESTAMPTZ NOT NULL,
    reason TEXT NOT NULL,
    amount NUMERIC(14, 2) NOT NULL CHECK (amount >= 0)
);

CREATE TABLE IF NOT EXISTS ecommerce.inventory_snapshots (
    snapshot_date DATE NOT NULL,
    product_id BIGINT NOT NULL REFERENCES ecommerce.products(product_id),
    stock_quantity INTEGER NOT NULL CHECK (stock_quantity >= 0),
    PRIMARY KEY (snapshot_date, product_id)
);

CREATE TABLE IF NOT EXISTS agent_app.anonymous_users (
    user_id TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agent_app.dataset_metadata (
    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    data_version TEXT NOT NULL,
    seed BIGINT NOT NULL,
    profile TEXT NOT NULL,
    result_hash TEXT NOT NULL,
    row_counts JSONB NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agent_app.conversations (
    conversation_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES agent_app.anonymous_users(user_id),
    title TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS conversations_user_updated_idx
    ON agent_app.conversations (user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS agent_app.messages (
    message_id BIGSERIAL PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES agent_app.conversations(conversation_id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'tool')),
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agent_app.stream_events (
    event_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES agent_app.conversations(conversation_id) ON DELETE CASCADE,
    request_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS stream_events_conversation_idx
    ON agent_app.stream_events (conversation_id, created_at, event_id);

CREATE TABLE IF NOT EXISTS agent_app.tool_calls (
    tool_call_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES agent_app.conversations(conversation_id) ON DELETE CASCADE,
    request_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    arguments JSONB NOT NULL,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agent_app.tool_results (
    tool_result_id BIGSERIAL PRIMARY KEY,
    tool_call_id TEXT NOT NULL REFERENCES agent_app.tool_calls(tool_call_id) ON DELETE CASCADE,
    success BOOLEAN NOT NULL,
    result_summary JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agent_app.evidence (
    evidence_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES agent_app.conversations(conversation_id) ON DELETE CASCADE,
    request_id TEXT NOT NULL,
    analysis_step TEXT NOT NULL,
    tool_call_id TEXT NOT NULL,
    claim TEXT NOT NULL,
    sql TEXT NOT NULL,
    columns_json JSONB NOT NULL,
    row_count INTEGER NOT NULL,
    result_hash TEXT NOT NULL,
    execution_time_ms DOUBLE PRECISION NOT NULL DEFAULT 0,
    preview_json JSONB NOT NULL,
    executed_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_app.charts (
    chart_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES agent_app.conversations(conversation_id) ON DELETE CASCADE,
    request_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL REFERENCES agent_app.evidence(evidence_id) ON DELETE CASCADE,
    chart_type TEXT NOT NULL,
    title TEXT NOT NULL,
    figure_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agent_app.memory_records (
    memory_id TEXT PRIMARY KEY,
    dedupe_key TEXT NOT NULL UNIQUE,
    question TEXT NOT NULL,
    analysis_step TEXT NOT NULL,
    normalized_sql TEXT NOT NULL,
    tables_and_columns JSONB NOT NULL,
    schema_fingerprint TEXT NOT NULL,
    metric_versions JSONB NOT NULL,
    execution_time_ms DOUBLE PRECISION NOT NULL DEFAULT 0,
    row_count INTEGER NOT NULL DEFAULT 0,
    column_names JSONB NOT NULL,
    limited_summary TEXT NOT NULL,
    result_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('candidate', 'trusted', 'stale', 'rejected')),
    source TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    last_verified_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS memory_records_status_idx
    ON agent_app.memory_records (status, created_at DESC);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'commerce_reader') THEN
        CREATE ROLE commerce_reader LOGIN PASSWORD 'commerce_reader';
    END IF;
END
$$;

REVOKE ALL ON SCHEMA agent_app FROM commerce_reader;
REVOKE ALL ON ALL TABLES IN SCHEMA agent_app FROM commerce_reader;
GRANT CONNECT ON DATABASE commerce_trace TO commerce_reader;
GRANT USAGE ON SCHEMA ecommerce TO commerce_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA ecommerce TO commerce_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA ecommerce GRANT SELECT ON TABLES TO commerce_reader;
