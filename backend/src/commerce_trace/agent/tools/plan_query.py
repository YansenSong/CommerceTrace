from __future__ import annotations

from typing import Annotated, Any

from langchain.tools import ToolRuntime, tool

from ...query_engine import QueryEngineError
from .context import AgentContext


@tool
async def plan_query(
    sql: Annotated[str, "仅访问 ecommerce 白名单表的单条只读 SQLite 查询"],
    purpose: Annotated[str, "本次查询要验证的分析目的"],
    runtime: ToolRuntime[AgentContext],
) -> dict[str, Any]:
    """校验并生成 EXPLAIN QUERY PLAN，不执行查询、不返回任何业务数据。

    执行 SQL 前必须先调用本工具查看执行计划；计划中的全表扫描或过大结果应在执行前收敛。
    """

    try:
        prepared = await runtime.context.query_engine.prepare(sql, purpose=purpose)
    except QueryEngineError as exc:
        return {
            "success": False,
            "phase": "prepare",
            "error": exc.code,
            "message": exc.safe_message,
        }
    return {
        "success": True,
        "phase": "prepare",
        **prepared.model_dump(mode="json"),
    }
