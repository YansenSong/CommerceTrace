from __future__ import annotations

from typing import Any

from ....models import Chart, ToolFailure, ToolSuccess
from ..base import Tool, ToolContext
from .args import VisualizeDataArgs


class VisualizeDataTool(Tool[VisualizeDataArgs]):
    """Generates a controlled Plotly JSON chart from an Evidence result."""

    _allowed_types = {"metric_card", "bar", "line", "pie"}

    @property
    def name(self) -> str:
        return "visualize_data"

    @property
    def description(self) -> str:
        return "从本次请求的 Evidence 结果生成受控 Plotly JSON"

    def get_args_schema(self) -> type[VisualizeDataArgs]:
        return VisualizeDataArgs

    async def execute(self, context: ToolContext, args: VisualizeDataArgs) -> ToolSuccess | ToolFailure:
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
        if args.chart_type not in self._allowed_types:
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
        figure = self._build_figure(args, rows)
        chart = Chart(
            evidence_id=args.evidence_id,
            chart_type=args.chart_type,  # type: ignore[arg-type]
            title=args.title,
            figure=figure,
        )
        return ToolSuccess(data={"chart": chart.model_dump(mode="json")})

    async def on_success(
        self, context: ToolContext, args: VisualizeDataArgs, result: ToolSuccess
    ) -> None:
        chart_data = result.data.get("chart")
        if chart_data is None:
            return
        chart = Chart.model_validate(chart_data)
        if context.store is not None:
            await context.store.save_chart(
                context.user_id, context.conversation_id, context.request_id, chart
            )
        context.created_charts.append(chart)

    def _build_figure(self, args: VisualizeDataArgs, rows: list[dict[str, Any]]) -> dict[str, Any]:
        if args.chart_type == "metric_card":
            value_field = args.value or args.y
            return {
                "data": [
                    {
                        "type": "indicator",
                        "mode": "number",
                        "value": rows[0].get(value_field) if rows and value_field else None,
                    }
                ],
                "layout": {"title": {"text": args.title}},
            }
        if args.chart_type == "pie":
            return {
                "data": [
                    {
                        "type": "pie",
                        "labels": [row.get(args.x) for row in rows],
                        "values": [row.get(args.y or args.value) for row in rows],
                    }
                ],
                "layout": {"title": {"text": args.title}},
            }
        trace_type = "scatter" if args.chart_type == "line" else args.chart_type
        trace: dict[str, Any] = {
            "type": trace_type,
            "x": [row.get(args.x) for row in rows],
            "y": [row.get(args.y or args.value) for row in rows],
        }
        if args.chart_type == "line":
            trace["mode"] = "lines+markers"
        return {"data": [trace], "layout": {"title": {"text": args.title}}}
