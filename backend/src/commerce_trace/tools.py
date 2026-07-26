from __future__ import annotations

import hashlib
import json
import time
from abc import ABC, abstractmethod
from typing import Any, Generic, Protocol, TypeVar

from pydantic import BaseModel, Field, ValidationError

from .contracts import Chart, ToolFailure, ToolResult, ToolSchema, ToolSuccess
from .memory import MemoryService
from .sql_safety import SqlSafetyError, SqlSafetyPolicy


class RunSqlArgs(BaseModel):
    sql: str = Field(min_length=1, max_length=20_000)
    purpose: str = Field(min_length=1, max_length=500)
    expected_columns: list[str] = Field(default_factory=list, max_length=30)


class VisualizeDataArgs(BaseModel):
    evidence_id: str
    chart_type: str
    title: str = Field(max_length=200)
    x: str | None = None
    y: str | None = None
    value: str | None = None


class SearchMemoryArgs(BaseModel):
    query: str = Field(min_length=1, max_length=1_000)


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


class ToolExecutionContext(BaseModel):
    user_id: str
    conversation_id: str
    request_id: str
    query_results: dict[str, dict[str, Any]] = Field(default_factory=dict)


ArgsT = TypeVar("ArgsT", bound=BaseModel)


class Tool(ABC, Generic[ArgsT]):
    name: str
    description: str
    args_model: type[ArgsT]

    @abstractmethod
    async def execute(self, context: ToolExecutionContext, args: ArgsT) -> ToolResult:
        raise NotImplementedError

    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=self.description,
            parameters=self.args_model.model_json_schema(),
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


class RunSqlTool(Tool[RunSqlArgs]):
    name = "run_sql"
    description = "执行一条有界的 PostgreSQL 只读业务查询"
    args_model = RunSqlArgs

    def __init__(self, executor: SqlExecutor, policy: SqlSafetyPolicy | None = None) -> None:
        self.executor = executor
        self.policy = policy or SqlSafetyPolicy()

    async def execute(self, context: ToolExecutionContext, args: RunSqlArgs) -> ToolResult:
        try:
            validated = self.policy.validate(args.sql)
        except SqlSafetyError as exc:
            return ToolFailure(
                safe_error_code=exc.code,
                safe_error_message=exc.safe_message,
                retryable=False,
            )
        started = time.perf_counter()
        try:
            rows = await self.executor.execute(validated.normalized_sql, validated.row_limit + 1)
        except Exception:
            return ToolFailure(
                safe_error_code="database_query_failed",
                safe_error_message="查询执行失败，请检查字段、筛选条件或聚合方式",
                retryable=True,
            )
        truncated = len(rows) > validated.row_limit
        rows = rows[: validated.row_limit]
        columns = list(rows[0].keys()) if rows else list(args.expected_columns)
        canonical = json.dumps(rows, ensure_ascii=False, sort_keys=True, default=str)
        result_hash = hashlib.sha256(canonical.encode()).hexdigest()
        data = {
            "sql": validated.normalized_sql,
            "purpose": args.purpose,
            "expected_columns": args.expected_columns,
            "columns": columns,
            "row_count": len(rows),
            "preview": rows,
            "execution_time_ms": round((time.perf_counter() - started) * 1000, 3),
            "result_hash": result_hash,
            "truncated": truncated,
        }
        result_id = f"result_{result_hash[:12]}"
        context.query_results[result_id] = data
        data["result_id"] = result_id
        return ToolSuccess(
            data=data,
            summary_for_llm=json.dumps(
                {
                    "result_id": result_id,
                    "columns": columns,
                    "row_count": len(rows),
                    "preview": rows[:20],
                    "result_hash": result_hash,
                },
                ensure_ascii=False,
                default=str,
            ),
        )


class VisualizeDataTool(Tool[VisualizeDataArgs]):
    name = "visualize_data"
    description = "从本次请求的 Evidence 结果生成受控 Plotly JSON"
    args_model = VisualizeDataArgs
    allowed_types = {"metric_card", "bar", "line", "pie"}

    async def execute(self, context: ToolExecutionContext, args: VisualizeDataArgs) -> ToolResult:
        evidence_result = next(
            (
                value
                for value in context.query_results.values()
                if value.get("evidence_id") == args.evidence_id
            ),
            None,
        )
        if evidence_result is None:
            return ToolFailure(
                safe_error_code="evidence_not_found",
                safe_error_message="图表引用的 Evidence 不属于本次请求",
            )
        if args.chart_type not in self.allowed_types:
            return ToolFailure(
                safe_error_code="unsupported_chart",
                safe_error_message="不支持该图表类型",
            )
        if args.chart_type == "metric_card" and not (args.value or args.y):
            return ToolFailure(
                safe_error_code="chart_field_required",
                safe_error_message="指标卡必须指定数值字段",
            )
        if args.chart_type != "metric_card" and (
            args.x is None or (args.y is None and args.value is None)
        ):
            return ToolFailure(
                safe_error_code="chart_field_required",
                safe_error_message="图表必须指定分类字段和数值字段",
            )
        columns = set(evidence_result["columns"])
        requested = {item for item in (args.x, args.y, args.value) if item}
        if not requested.issubset(columns):
            return ToolFailure(
                safe_error_code="chart_field_not_found",
                safe_error_message="图表字段不存在于查询结果",
            )
        rows = evidence_result["preview"]
        if args.chart_type == "metric_card":
            value_field = args.value or args.y
            figure: dict[str, Any] = {
                "data": [
                    {
                        "type": "indicator",
                        "mode": "number",
                        "value": rows[0].get(value_field) if rows and value_field else None,
                    }
                ],
                "layout": {"title": {"text": args.title}},
            }
        else:
            trace_type = "scatter" if args.chart_type == "line" else args.chart_type
            trace: dict[str, Any] = {
                "type": trace_type,
                "x": [row.get(args.x) for row in rows],
                "y": [row.get(args.y or args.value) for row in rows],
            }
            if args.chart_type == "line":
                trace["mode"] = "lines+markers"
            if args.chart_type == "pie":
                trace = {
                    "type": "pie",
                    "labels": [row.get(args.x) for row in rows],
                    "values": [row.get(args.y or args.value) for row in rows],
                }
            figure = {"data": [trace], "layout": {"title": {"text": args.title}}}
        chart = Chart(
            evidence_id=args.evidence_id,
            chart_type=args.chart_type,  # type: ignore[arg-type]
            title=args.title,
            figure=figure,
        )
        return ToolSuccess(
            data={"chart": chart.model_dump(mode="json")},
            summary_for_llm=f"图表已创建：{chart.chart_id}",
        )


class SearchMemoryTool(Tool[SearchMemoryArgs]):
    name = "search_memory"
    description = "按原始问题和当前步骤检索业务规则与工具经验"
    args_model = SearchMemoryArgs

    def __init__(self, memory: MemoryService) -> None:
        self.memory = memory

    async def execute(self, context: ToolExecutionContext, args: SearchMemoryArgs) -> ToolResult:
        results = await self.memory.search(args.query, limit_candidates=2)
        data = [
            {
                "memory_id": result.record.memory_id,
                "label": result.label,
                "question": result.record.question,
                "sql": result.record.normalized_sql,
                "score": result.score,
            }
            for result in results
        ]
        return ToolSuccess(
            data={"results": data},
            summary_for_llm=json.dumps(data, ensure_ascii=False),
        )


def build_default_registry(
    executor: SqlExecutor,
    memory: MemoryService,
    policy: SqlSafetyPolicy | None = None,
) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(RunSqlTool(executor=executor, policy=policy))
    registry.register(VisualizeDataTool())
    registry.register(SearchMemoryTool(memory))
    return registry
