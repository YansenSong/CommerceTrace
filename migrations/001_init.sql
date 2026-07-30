PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS customers (
    customer_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    region TEXT NOT NULL,
    registered_at TEXT NOT NULL,
    acquisition_channel TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS categories (
    category_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS products (
    product_id INTEGER PRIMARY KEY,
    category_id INTEGER NOT NULL REFERENCES categories(category_id),
    name TEXT NOT NULL,
    current_price REAL NOT NULL CHECK (current_price >= 0),
    current_cost REAL NOT NULL CHECK (current_cost >= 0)
);

CREATE TABLE IF NOT EXISTS orders (
    order_id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(customer_id),
    ordered_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('paid', 'completed', 'cancelled', 'refunded')),
    channel TEXT NOT NULL,
    total_amount REAL NOT NULL CHECK (total_amount >= 0)
);

CREATE TABLE IF NOT EXISTS order_items (
    order_item_id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(order_id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL REFERENCES products(product_id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    unit_price REAL NOT NULL CHECK (unit_price >= 0),
    unit_cost REAL NOT NULL CHECK (unit_cost >= 0)
);

CREATE TABLE IF NOT EXISTS payments (
    payment_id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL UNIQUE REFERENCES orders(order_id) ON DELETE CASCADE,
    paid_at TEXT NOT NULL,
    payment_method TEXT NOT NULL,
    amount REAL NOT NULL CHECK (amount >= 0)
);

CREATE TABLE IF NOT EXISTS refunds (
    refund_id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(order_id) ON DELETE CASCADE,
    refunded_at TEXT NOT NULL,
    reason TEXT NOT NULL,
    amount REAL NOT NULL CHECK (amount >= 0)
);

CREATE TABLE IF NOT EXISTS inventory_snapshots (
    snapshot_date TEXT NOT NULL,
    product_id INTEGER NOT NULL REFERENCES products(product_id),
    stock_quantity INTEGER NOT NULL CHECK (stock_quantity >= 0),
    PRIMARY KEY (snapshot_date, product_id)
);

CREATE TABLE IF NOT EXISTS dataset_metadata (
    singleton INTEGER PRIMARY KEY DEFAULT 1 CHECK (singleton = 1),
    data_version TEXT NOT NULL,
    seed INTEGER NOT NULL,
    profile TEXT NOT NULL,
    result_hash TEXT NOT NULL,
    row_counts TEXT NOT NULL,
    generated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

