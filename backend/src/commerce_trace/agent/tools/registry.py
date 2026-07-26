from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from ...contracts import ToolFailure, ToolResult, ToolSchema
from ...sql_safety import SqlSafetyPolicy
from .definitions import (
    RunSqlTool,
    SqlExecutor,
    Tool,
    ToolExecutionContext,
    VisualizeDataTool,
)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool[Any]] = {}

    def register(self, tool: Tool[Any]) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool: {tool.name}")
        self._tools[tool.name] = tool

    def schemas(self) -> list[ToolSchema]:
        return [tool.schema() for tool in self._tools.values()]

    async def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolFailure(
                safe_error_code="tool_not_found",
                safe_error_message="请求的工具不可用",
                retryable=False,
            )
        try:
            args = tool.args_model.model_validate(arguments)
        except ValidationError:
            return ToolFailure(
                safe_error_code="invalid_tool_arguments",
                safe_error_message="工具参数不符合约束",
                retryable=True,
            )
        try:
            return await tool.execute(context, args)
        except Exception:
            return ToolFailure(
                safe_error_code="tool_execution_failed",
                safe_error_message="工具执行失败",
                retryable=True,
            )


def build_default_registry(
    executor: SqlExecutor,
    policy: SqlSafetyPolicy | None = None,
) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(RunSqlTool(executor=executor, policy=policy))
    registry.register(VisualizeDataTool())
    return registry
