from __future__ import annotations

import sqlite3
import time
from typing import Annotated, Any
from uuid import uuid4

from langchain.tools import ToolRuntime, tool

from ...models import QueryTrace
from ..sql_safety import SqlSafetyError
from .context import AgentContext


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
            "error": exc.code,
            "message": exc.safe_message,
        }

    started = time.perf_counter()
    try:
        async with artifacts.semaphore:
            rows = await context.executor.execute(
                validated.normalized_sql,
                validated.row_limit,
            )
    except sqlite3.Error:
        return {
            "success": False,
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
    )
    async with artifacts.lock:
        artifacts.queries.append(trace)
        artifacts.rows_by_query[query_id] = rows
    return {
        "success": True,
        "query_id": query_id,
        "columns": columns,
        "row_count": len(rows),
        "rows": rows[:20],
        "preview_truncated": len(rows) > 20,
    }
