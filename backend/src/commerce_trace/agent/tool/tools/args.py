"""内置 Agent 工具使用的参数模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class RunSqlArgs(BaseModel):
    """约束只读 SQL 工具接收的查询、用途和预期列。"""

    sql: str = Field(min_length=1, max_length=20_000)
    purpose: str = Field(min_length=1, max_length=500)
    expected_columns: list[str] = Field(default_factory=list, max_length=30)


class VisualizeDataArgs(BaseModel):
    """约束可视化工具引用的证据、图表类型和字段。"""

    evidence_id: str
    chart_type: str
    title: str = Field(max_length=200)
    x: str | None = None
    y: str | None = None
    value: str | None = None
