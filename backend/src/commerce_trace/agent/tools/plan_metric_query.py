from __future__ import annotations

from typing import Annotated, Any

from langchain.tools import ToolRuntime, tool

from ...query_engine import QueryEngineError
from .context import AgentContext


@tool
async def plan_metric_query(
    metric: Annotated[str, "业务指标 ID、名称或同义词"],
    dimension_ids: Annotated[list[str], "可选的受治理维度 ID"],
    purpose: Annotated[str, "本次查询要验证的分析目的"],
    runtime: ToolRuntime[AgentContext],
) -> dict[str, Any]:
    """按版本化业务口径展开指标，并生成可审查的执行计划。"""

    try:
        prepared = await runtime.context.query_engine.prepare_metric(
            metric,
            dimension_ids=tuple(dimension_ids),
            purpose=purpose,
        )
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
        "governed_metric": metric,
        "dimension_ids": dimension_ids,
        **prepared.model_dump(mode="json"),
    }
