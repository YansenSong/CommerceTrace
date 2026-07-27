from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError

from ...contracts import ToolFailure, ToolResult, ToolSchema, ToolSuccess
from ..sql_safety import SqlSafetyPolicy
from .base import SqlExecutor, Tool, ToolContext
from .run_sql import RunSqlTool
from .visualize_data import VisualizeDataTool

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Central registry that owns tool registration, argument transformation,
    execution, and lifecycle hook dispatch.
    """

    def __init__(self) -> None:
        self._tools: dict[str, Tool[Any]] = {}

    def register(self, tool: Tool[Any]) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool: {tool.name}")
        self._tools[tool.name] = tool

    def tool_kind(self, name: str) -> str:
        """Return the ``kind`` category of a registered tool, or ``"default"``."""
        tool = self._tools.get(name)
        return tool.kind if tool else "default"

    def schemas(self) -> list[ToolSchema]:
        return [tool.schema() for tool in self._tools.values()]

    async def transform_args(
        self, tool: Tool[Any], args: Any, context: ToolContext
    ) -> Any:
        """Hook point for cross-cutting argument transformation (enrichment,
        row-level security, rejection).  The default is a no-op — override in
        subclasses.
        """
        return args

    async def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        context: ToolContext,
    ) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolFailure(
                safe_error_code="tool_not_found",
                safe_error_message="请求的工具不可用",
                retryable=False,
            )
        try:
            parsed = tool.get_args_schema().model_validate(arguments)
        except ValidationError:
            return ToolFailure(
                safe_error_code="invalid_tool_arguments",
                safe_error_message="工具参数不符合约束",
                retryable=True,
            )

        try:
            transformed = await self.transform_args(tool, parsed, context)
        except Exception:
            return ToolFailure(
                safe_error_code="tool_args_transform_failed",
                safe_error_message="工具参数预处理失败",
                retryable=False,
            )

        try:
            result = await tool.execute(context, transformed)
        except Exception:
            failure = ToolFailure(
                safe_error_code="tool_execution_failed",
                safe_error_message="工具执行失败",
                retryable=True,
            )
            try:
                await tool.on_failure(context, parsed, failure)
            except Exception:
                logger.debug("on_failure hook raised", exc_info=True)
            return failure

        if isinstance(result, ToolSuccess):
            try:
                await tool.on_success(context, parsed, result)
            except Exception:
                logger.debug("on_success hook raised", exc_info=True)
        elif isinstance(result, ToolFailure):
            try:
                await tool.on_failure(context, parsed, result)
            except Exception:
                logger.debug("on_failure hook raised", exc_info=True)

        return result


def build_default_registry(
    executor: SqlExecutor,
    policy: SqlSafetyPolicy | None = None,
) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(RunSqlTool(executor=executor, policy=policy))
    registry.register(VisualizeDataTool())
    return registry
