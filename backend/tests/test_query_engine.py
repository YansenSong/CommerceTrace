from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path

from commerce_trace.agent.sql_safety import SqlSafetyPolicy
from commerce_trace.query_engine import QueryEngine, QueryEngineError
from commerce_trace.semantic import COMMERCE_SEMANTIC_MODEL


class QueryEngineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary_directory.cleanup)
        self.database_path = Path(self._temporary_directory.name) / "business.db"
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute(
                """
                CREATE TABLE orders (
                    order_id INTEGER PRIMARY KEY,
                    status TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    total_amount REAL NOT NULL
                )
                """
            )
            connection.executemany(
                "INSERT INTO orders VALUES (?, ?, ?, ?)",
                [
                    (1, "paid", "app", 120.0),
                    (2, "completed", "web", 80.0),
                ],
            )
            connection.commit()
        finally:
            connection.close()
        self.engine = QueryEngine(
            database_path=self.database_path,
            statement_timeout_ms=1_000,
            sql_policy=SqlSafetyPolicy(semantic_model=COMMERCE_SEMANTIC_MODEL),
            semantic_model=COMMERCE_SEMANTIC_MODEL,
        )

    async def test_query_must_be_prepared_and_execution_is_idempotent(self) -> None:
        prepared = await asyncio.wait_for(
            self.engine.prepare(
                "SELECT SUM(total_amount) AS revenue FROM ecommerce.orders",
                purpose="计算销售额",
            ),
            timeout=2,
        )

        self.assertTrue(prepared.prepared_query_id.startswith("prepared_"))
        self.assertEqual(prepared.semantic_fingerprint, COMMERCE_SEMANTIC_MODEL.fingerprint())
        self.assertIn("SUM(total_amount)", prepared.normalized_sql)

        first = await asyncio.wait_for(
            self.engine.execute(prepared.prepared_query_id),
            timeout=2,
        )
        second = await asyncio.wait_for(
            self.engine.execute(prepared.prepared_query_id),
            timeout=2,
        )

        self.assertEqual(first.trace.query_id, second.trace.query_id)
        self.assertEqual(first.rows, [{"revenue": 200.0}])
        self.assertFalse(first.trace.truncated)

    async def test_unknown_prepared_query_cannot_execute(self) -> None:
        with self.assertRaisesRegex(QueryEngineError, "prepared_query_not_found"):
            await self.engine.execute("prepared_missing")


if __name__ == "__main__":
    unittest.main()
