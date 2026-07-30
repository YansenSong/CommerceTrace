from __future__ import annotations

from typing import Annotated, Any

from langchain.tools import ToolRuntime, tool

from ..prompt import SCHEMA_CATALOG
from .context import AgentContext


@tool
async def get_schema(
    tables: Annotated[
        list[str] | None,
        "需要查看的表名；省略时返回全部允许访问的业务表",
    ] = None,
    *,
    runtime: ToolRuntime[AgentContext],
) -> dict[str, Any]:
    """读取允许访问的 ecommerce 表结构，不返回任何业务数据。"""

    async with runtime.context.artifacts.semaphore:
        requested = tables or list(SCHEMA_CATALOG["tables"])
        unknown = sorted(set(requested) - set(SCHEMA_CATALOG["tables"]))
        if unknown:
            return {
                "success": False,
                "error": "schema_table_denied",
                "message": "请求包含不允许访问的表",
            }
        return {
            "success": True,
            "schema": "ecommerce",
            "tables": {
                name: SCHEMA_CATALOG["tables"][name]
                for name in requested
            },
        }
