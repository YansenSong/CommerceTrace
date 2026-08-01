from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

from langchain.tools import ToolRuntime, tool

from ...models import QueryTrace
from ..sql_safety import SqlSafetyError
from .context import AgentContext


async def _execute_sql(
    database_path: Path,
    sql: str,
    row_limit: int,
    statement_timeout_ms: int,
) -> tuple[list[dict[str, Any]], bool]:
    """执行只读 SQL，返回 (行数据, 是否超过行数上限被截断)。

    EXPLAIN 类语句不包装子查询，直接执行规划器输出，无截断。
    """

    def operation() -> tuple[list[dict[str, Any]], bool]:
        connection = sqlite3.connect(":memory:", check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute(
            "ATTACH DATABASE ? AS ecommerce",
            (str(database_path),),
        )
        connection.execute("PRAGMA query_only = ON")
        deadline = time.monotonic() + statement_timeout_ms / 1_000
        connection.set_progress_handler(
            lambda: 1 if time.monotonic() > deadline else 0,
            1_000,
        )
        try:
            if sql.upper().startswith("EXPLAIN "):
                cursor = connection.execute(sql)
                rows = [dict(row) for row in cursor.fetchall()]
                return rows, False
            cursor = connection.execute(
                f"SELECT * FROM ({sql}) AS commerce_trace_result LIMIT ?",
                (row_limit + 1,),
            )
            fetched = [dict(row) for row in cursor.fetchall()]
            truncated = len(fetched) > row_limit
            return fetched[:row_limit], truncated
        finally:
            connection.close()

    return await asyncio.to_thread(operation)


@tool
async def run_sql(
    sql: Annotated[str, "仅访问 ecommerce 白名单表的单条只读 SQLite 查询"],
    purpose: Annotated[str, "本次查询要验证的分析目的"],
    runtime: ToolRuntime[AgentContext],
) -> dict[str, Any]:
    """校验并执行只读 SQL；失败时根据安全错误修正查询，禁止猜测结果。"""

    context = runtime.context
    artifacts = context.artifacts
    try:
        validated = context.sql_policy.validate(sql)
    except SqlSafetyError as exc:
        return {
            "success": False,
            "phase": "validate",
            "error": exc.code,
            "message": exc.safe_message,
        }

    started = time.perf_counter()
    try:
        rows, truncated = await _execute_sql(
            context.database_path,
            validated.normalized_sql,
            validated.row_limit,
            context.statement_timeout_ms,
        )
    except sqlite3.Error:
        return {
            "success": False,
            "phase": "execute",
            "error": "query_failed",
            "message": "查询执行失败，请检查字段、筛选条件或聚合方式",
        }

    query_id = f"query_{uuid4().hex[:12]}"
    columns = list(rows[0]) if rows else []
    trace = QueryTrace(
        query_id=query_id,
        purpose=purpose,
        sql=validated.normalized_sql,
        columns=columns,
        row_count=len(rows),
        preview=rows[:20],
        execution_time_ms=(time.perf_counter() - started) * 1_000,
        truncated=truncated,
    )
    artifacts.queries.append(trace)
    artifacts.rows_by_query[query_id] = rows
    return {
        "success": True,
        "query_id": query_id,
        "columns": columns,
        "row_count": len(rows),
        "truncated": truncated,
        "rows": rows[:20],
        "preview_truncated": len(rows) > 20,
    }
