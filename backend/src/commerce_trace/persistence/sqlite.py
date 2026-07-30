from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path
from typing import Any


def connect_business(path: Path, *, read_only: bool = False) -> sqlite3.Connection:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if read_only:
        connection = sqlite3.connect(
            f"{path.as_uri()}?mode=ro",
            uri=True,
            check_same_thread=False,
        )
        connection.execute("PRAGMA query_only = ON")
    else:
        connection = sqlite3.connect(path, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


class BusinessDatabase:
    def __init__(self, path: Path) -> None:
        self.path = path

    async def health(self) -> bool:
        def check() -> bool:
            try:
                with connect_business(self.path, read_only=True) as connection:
                    row = connection.execute(
                        "SELECT EXISTS (SELECT 1 FROM orders)"
                    ).fetchone()
                    return bool(row and row[0])
            except sqlite3.Error:
                return False

        return await asyncio.to_thread(check)


class SQLiteSqlExecutor:
    """Runs isolated, read-only ecommerce queries with a hard deadline."""

    def __init__(self, database_path: Path, *, statement_timeout_ms: int = 5_000) -> None:
        self.database_path = database_path.resolve()
        self.statement_timeout_ms = statement_timeout_ms

    async def execute(self, sql: str, row_limit: int) -> list[dict[str, Any]]:
        def operation() -> list[dict[str, Any]]:
            connection = sqlite3.connect(":memory:", check_same_thread=False)
            connection.row_factory = sqlite3.Row
            connection.execute(
                "ATTACH DATABASE ? AS ecommerce",
                (str(self.database_path),),
            )
            connection.execute("PRAGMA query_only = ON")
            deadline = time.monotonic() + self.statement_timeout_ms / 1000
            connection.set_progress_handler(
                lambda: 1 if time.monotonic() > deadline else 0,
                1_000,
            )
            try:
                if sql.upper().startswith("EXPLAIN "):
                    cursor = connection.execute(sql)
                else:
                    cursor = connection.execute(
                        f"SELECT * FROM ({sql}) AS commerce_trace_result LIMIT ?",
                        (row_limit,),
                    )
                return [dict(row) for row in cursor.fetchall()]
            finally:
                connection.close()

        return await asyncio.to_thread(operation)
