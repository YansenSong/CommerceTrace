from __future__ import annotations

from typing import Annotated, Any

from langchain.tools import ToolRuntime, tool

from ..prompt import SCHEMA_CATALOG, compact_catalog
from .context import AgentContext


@tool
async def get_schema(
    tables: Annotated[
        list[str] | None,
        "需要查看具体列结构的表名；省略时返回紧凑表目录（无列级细节）",
    ] = None,
    *,
    runtime: ToolRuntime[AgentContext],
) -> dict[str, Any]:
    """读取允许访问的 ecommerce 表结构，不返回任何业务数据。"""

    if not tables:
        return {
            "success": True,
            "schema": "ecommerce",
            "compact_catalog": compact_catalog(),
        }
    unknown = sorted(set(tables) - set(SCHEMA_CATALOG["tables"]))
    if unknown:
        return {
            "success": False,
            "error": "schema_table_denied",
            "message": "请求包含不允许访问的表",
        }
    runtime.context.query_engine.acquire_tables(tables)
    return {
        "success": True,
        "schema": "ecommerce",
        "tables": {
            name: SCHEMA_CATALOG["tables"][name]
            for name in tables
        },
    }
