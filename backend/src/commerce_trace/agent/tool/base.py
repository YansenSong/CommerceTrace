from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Generic, Protocol, TypeVar

from pydantic import BaseModel, Field

from ...models import Chart, Evidence, ToolFailure, ToolResult, ToolSchema, ToolSuccess


class ToolContext(BaseModel):
    """注入每次工具调用的执行上下文。

    保存请求身份、同一请求内工具间共享的可变状态，以及生命周期钩子所需的服务。
    """

    user_id: str = ""
    conversation_id: str = ""
    request_id: str = ""
    tool_call_id: str = ""
    query_results: dict[str, dict[str, Any]] = Field(default_factory=dict)
    # 由 Agent 注入，使生命周期钩子能够持久化结果。
    store: Any = Field(default=None, exclude=True)
    # 由生命周期钩子填充，并在每次执行后交给 Agent 消费。
    created_evidence: list[Evidence] = Field(default_factory=list)
    created_charts: list[Chart] = Field(default_factory=list)

    model_config = {"arbitrary_types_allowed": True}


ArgsT = TypeVar("ArgsT", bound=BaseModel)


class Tool(ABC, Generic[ArgsT]):
    """定义所有 Agent 工具必须遵循的抽象接口。

    每个工具需要声明身份、参数模型和执行逻辑；可选的成功/失败钩子负责
    持久化与共享状态更新等副作用，使 Agent 主循环保持通用。
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """返回工具在注册表中的唯一名称。"""

        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """返回提供给大模型的工具用途说明。"""

        ...

    @abstractmethod
    def get_args_schema(self) -> type[ArgsT]:
        """返回用于参数校验和生成 JSON Schema 的 Pydantic 模型。"""

        ...

    @abstractmethod
    async def execute(self, context: ToolContext, args: ArgsT) -> ToolResult:
        """在给定上下文中执行经过校验的工具参数。"""

        ...

    async def on_success(self, context: ToolContext, args: ArgsT, result: ToolSuccess) -> None:
        """在工具成功后执行证据创建、图表持久化等可选后处理。"""

    async def on_failure(self, context: ToolContext, args: ArgsT, error: ToolFailure) -> None:
        """在工具失败后执行可选的错误后处理。"""

    def schema(self) -> ToolSchema:
        """生成可提供给大模型的工具 JSON Schema 描述。"""

        return ToolSchema(
            name=self.name,
            description=self.description,
            parameters=self.get_args_schema().model_json_schema(),
        )


class SqlExecutor(Protocol):
    """定义只读 SQL 执行器所需的最小接口。"""

    async def execute(self, sql: str, row_limit: int) -> list[dict[str, Any]]:
        """执行 SQL 并返回不超过指定上限的字典行。"""

        ...


class FakeSqlExecutor:
    """提供固定结果和可预设失败的 SQL 执行器测试替身。"""

    def __init__(
        self,
        rows: list[dict[str, Any]] | None = None,
        failures: list[Exception] | None = None,
    ) -> None:
        """设置固定返回行与按顺序触发的异常。"""

        self.rows = rows if rows is not None else [{"revenue": 1000.0}]
        self.failures = list(failures or [])
        self.executed: list[str] = []

    async def execute(self, sql: str, row_limit: int) -> list[dict[str, Any]]:
        """记录 SQL，在需要时抛出预设异常，否则返回固定行。"""

        self.executed.append(sql)
        if self.failures:
            raise self.failures.pop(0)
        return self.rows[:row_limit]
