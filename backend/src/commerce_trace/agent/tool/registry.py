from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError

from ...models import ToolFailure, ToolResult, ToolSchema, ToolSuccess
from ..sql_safety import SqlSafetyPolicy
from .base import SqlExecutor, Tool, ToolContext
from .tools.run_sql import RunSqlTool
from .tools.visualize_data import VisualizeDataTool

logger = logging.getLogger(__name__)


class ToolRegistry:
    """集中管理工具注册、参数转换、执行和生命周期钩子分发。"""

    def __init__(self) -> None:
        """初始化一个空的工具名称到工具实例映射。"""

        self._tools: dict[str, Tool[Any]] = {}

    def register(self, tool: Tool[Any]) -> None:
        """注册工具，并拒绝名称重复的工具。"""

        if tool.name in self._tools:
            raise ValueError(f"duplicate tool: {tool.name}")
        self._tools[tool.name] = tool

    def schemas(self) -> list[ToolSchema]:
        """返回全部已注册工具的大模型可读 Schema。"""

        return [tool.schema() for tool in self._tools.values()]

    async def transform_args(self, tool: Tool[Any], args: Any, context: ToolContext) -> Any:
        """提供参数增强、行级安全或拒绝等横切转换扩展点。"""

        return args

    async def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        context: ToolContext,
    ) -> ToolResult:
        """校验并执行指定工具，同时分发生命周期钩子。"""

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
    """构建包含只读 SQL 和受控可视化工具的默认注册表。"""

    registry = ToolRegistry()
    registry.register(RunSqlTool(executor=executor, policy=policy))
    registry.register(VisualizeDataTool())
    return registry
