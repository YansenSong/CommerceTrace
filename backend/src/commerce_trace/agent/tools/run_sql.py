from __future__ import annotations

from typing import Annotated, Any

from langchain.tools import ToolRuntime, tool

from ...query_engine import QueryEngineError
from .context import AgentContext


@tool
async def run_sql(
    prepared_query_id: Annotated[
        str,
        "本次运行中 plan_query 返回的 prepared_query_id",
    ],
    runtime: ToolRuntime[AgentContext],
) -> dict[str, Any]:
    """执行已经准备和校验的只读查询；不接受任意 SQL。"""

    context = runtime.context
    artifacts = context.artifacts
    try:
        result = await context.query_engine.execute(prepared_query_id)
    except QueryEngineError as exc:
        return {
            "success": False,
            "phase": "execute",
            "error": exc.code,
            "message": exc.safe_message,
        }
    trace = result.trace
    if all(existing.query_id != trace.query_id for existing in artifacts.queries):
        artifacts.queries.append(trace)
    artifacts.rows_by_query[trace.query_id] = result.rows
    return {
        "success": True,
        "query_id": trace.query_id,
        "columns": trace.columns,
        "row_count": trace.row_count,
        "truncated": trace.truncated,
        "rows": result.rows[:20],
        "preview_truncated": len(result.rows) > 20,
    }
