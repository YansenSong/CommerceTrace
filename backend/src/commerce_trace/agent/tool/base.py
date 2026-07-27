from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Generic, Protocol, TypeVar

from pydantic import BaseModel, Field

from ...models import Chart, Evidence, ToolFailure, ToolResult, ToolSchema, ToolSuccess


class ToolContext(BaseModel):
    """Execution context injected into every tool invocation.

    Carries request identity, shared mutable state between tool calls
    within a single request, and injected services for lifecycle hooks.
    """

    user_id: str = ""
    conversation_id: str = ""
    request_id: str = ""
    tool_call_id: str = ""
    query_results: dict[str, dict[str, Any]] = Field(default_factory=dict)
    # Injected by the agent so lifecycle hooks can persist results
    store: Any = Field(default=None, exclude=True)
    # Populated by tool lifecycle hooks; consumed by the agent after each execution
    created_evidence: list[Evidence] = Field(default_factory=list)
    created_charts: list[Chart] = Field(default_factory=list)

    model_config = {"arbitrary_types_allowed": True}


ArgsT = TypeVar("ArgsT", bound=BaseModel)


class Tool(ABC, Generic[ArgsT]):
    """Abstract base for agent tools.

    Every tool declares:
      - Identity: name (unique id) + description (LLM-facing)
      - Contract: get_args_schema() returns the Pydantic model for arguments
      - Logic: execute(context, args) -> ToolResult

    Optional lifecycle hooks (on_success / on_failure) handle side effects
    (persistence, shared-state updates) so the agent loop stays generic.
    """

    @property
    def kind(self) -> str:
        """Tool category for budget tracking.  'business_sql' tools receive
        SQL-specific rate limits.  Override in subclasses as needed."""
        return "default"

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    @abstractmethod
    def get_args_schema(self) -> type[ArgsT]:
        """Pydantic model class used for validation and JSON Schema generation."""
        ...

    @abstractmethod
    async def execute(self, context: ToolContext, args: ArgsT) -> ToolResult: ...

    async def on_success(self, context: ToolContext, args: ArgsT, result: ToolSuccess) -> None:
        """Called by the registry after a successful execution.  Override for
        tool-specific post-processing (creating evidence, persisting charts, etc.).
        """

    async def on_failure(self, context: ToolContext, args: ArgsT, error: ToolFailure) -> None:
        """Called by the registry after a failed execution."""

    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=self.description,
            parameters=self.get_args_schema().model_json_schema(),
        )


class SqlExecutor(Protocol):
    async def execute(self, sql: str, row_limit: int) -> list[dict[str, Any]]: ...


class FakeSqlExecutor:
    def __init__(
        self,
        rows: list[dict[str, Any]] | None = None,
        failures: list[Exception] | None = None,
    ) -> None:
        self.rows = rows if rows is not None else [{"revenue": 1000.0}]
        self.failures = list(failures or [])
        self.executed: list[str] = []

    async def execute(self, sql: str, row_limit: int) -> list[dict[str, Any]]:
        self.executed.append(sql)
        if self.failures:
            raise self.failures.pop(0)
        return self.rows[:row_limit]
