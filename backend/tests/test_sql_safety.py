import pytest

from commerce_trace.agent.sql_safety import SqlSafetyError, SqlSafetyPolicy


@pytest.fixture
def policy() -> SqlSafetyPolicy:
    return SqlSafetyPolicy()


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT region, COUNT(*) FROM ecommerce.customers GROUP BY region",
        "WITH totals AS (SELECT SUM(quantity) q FROM ecommerce.order_items) SELECT * FROM totals",
        "EXPLAIN SELECT * FROM ecommerce.orders",
    ],
)
def test_allows_single_read_only_statement(policy: SqlSafetyPolicy, sql: str) -> None:
    result = policy.validate(sql)
    assert result.normalized_sql
    assert result.row_limit in {50, 500}


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM ecommerce.orders",
        "UPDATE ecommerce.orders SET status='paid'",
        "DROP TABLE ecommerce.orders",
        "SELECT * FROM agent_app.messages",
        "SELECT pg_sleep(10)",
        "SELECT 1; SELECT 2",
    ],
)
def test_rejects_dangerous_or_out_of_scope_sql(policy: SqlSafetyPolicy, sql: str) -> None:
    with pytest.raises(SqlSafetyError):
        policy.validate(sql)


def test_distinct_exploration_is_limited_and_whitelisted(policy: SqlSafetyPolicy) -> None:
    result = policy.validate("SELECT DISTINCT region FROM ecommerce.customers")
    assert result.row_limit == 50

    with pytest.raises(SqlSafetyError):
        policy.validate("SELECT DISTINCT name FROM ecommerce.customers")
