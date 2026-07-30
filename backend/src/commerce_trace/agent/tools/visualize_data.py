from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import uuid4

from langchain.tools import ToolRuntime, tool

from ...models import Chart
from .context import AgentContext


@tool
async def visualize_data(
    query_id: Annotated[str, "本次请求中 run_sql 返回的 query_id"],
    chart_type: Annotated[
        Literal["metric_card", "bar", "line", "pie"],
        "图表类型",
    ],
    title: Annotated[str, "简洁的中文图表标题"],
    runtime: ToolRuntime[AgentContext],
    x: Annotated[str | None, "分类、日期或横轴字段"] = None,
    y: Annotated[str | None, "数值或纵轴字段"] = None,
    value: Annotated[str | None, "指标卡数值字段"] = None,
) -> dict[str, Any]:
    """把本次请求的 SQL 查询结果转换为受控 Plotly figure。"""

    artifacts = runtime.context.artifacts
    async with artifacts.semaphore:
        rows = artifacts.rows_by_query.get(query_id)
        if rows is None:
            return {
                "success": False,
                "error": "query_not_found",
                "message": "图表只能引用本次请求已完成的查询",
            }
        columns = set(rows[0]) if rows else set()
        requested = {item for item in (x, y, value) if item}
        if not requested.issubset(columns):
            return {
                "success": False,
                "error": "chart_field_not_found",
                "message": "图表字段不存在于查询结果",
            }
        figure = _figure(
            rows=rows,
            chart_type=chart_type,
            title=title,
            x=x,
            y=y,
            value=value,
        )
        if figure is None:
            return {
                "success": False,
                "error": "chart_field_required",
                "message": "图表缺少必要的分类或数值字段",
            }
        chart = Chart(
            chart_id=f"chart_{uuid4().hex[:12]}",
            source_query_id=query_id,
            chart_type=chart_type,
            title=title,
            figure=figure,
        )
        async with artifacts.lock:
            artifacts.charts.append(chart)
        return {"success": True, "chart": chart.model_dump(mode="json")}


def _figure(
    *,
    rows: list[dict[str, Any]],
    chart_type: Literal["metric_card", "bar", "line", "pie"],
    title: str,
    x: str | None,
    y: str | None,
    value: str | None,
) -> dict[str, Any] | None:
    value_field = value or y
    if chart_type == "metric_card":
        if not value_field:
            return None
        return {
            "data": [
                {
                    "type": "indicator",
                    "mode": "number",
                    "value": rows[0].get(value_field) if rows else None,
                }
            ],
            "layout": {"title": {"text": title}},
        }
    if not x or not value_field:
        return None
    if chart_type == "pie":
        return {
            "data": [
                {
                    "type": "pie",
                    "labels": [row.get(x) for row in rows],
                    "values": [row.get(value_field) for row in rows],
                }
            ],
            "layout": {"title": {"text": title}},
        }
    trace: dict[str, Any] = {
        "type": "scatter" if chart_type == "line" else "bar",
        "x": [row.get(x) for row in rows],
        "y": [row.get(value_field) for row in rows],
    }
    if chart_type == "line":
        trace["mode"] = "lines+markers"
    return {"data": [trace], "layout": {"title": {"text": title}}}
