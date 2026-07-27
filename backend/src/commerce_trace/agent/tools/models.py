"""Argument models for built-in agent tools."""

from __future__ import annotations

from pydantic import BaseModel, Field


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
