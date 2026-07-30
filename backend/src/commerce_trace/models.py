from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


class QueryTrace(BaseModel):
    query_id: str
    purpose: str
    sql: str
    columns: list[str] = Field(default_factory=list)
    row_count: int = 0
    preview: list[dict[str, Any]] = Field(default_factory=list)
    execution_time_ms: float = 0


class Chart(BaseModel):
    chart_id: str
    source_query_id: str
    chart_type: Literal["metric_card", "bar", "line", "pie"]
    title: str
    figure: dict[str, Any]


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0


class ConversationSummary(BaseModel):
    conversation_id: str
    title: str
    created_at: datetime
    updated_at: datetime


class ConversationList(BaseModel):
    items: list[ConversationSummary]
    limit: int
    offset: int


class ConversationCreate(BaseModel):
    conversation_id: str
    title: str
    created_at: datetime
    updated_at: datetime


class MessageCreate(BaseModel):
    message: str = Field(min_length=1, max_length=4_000)


class MessageRecord(BaseModel):
    message_id: int
    role: Literal["user", "assistant"]
    content: str
    queries: list[QueryTrace] = Field(default_factory=list)
    charts: list[Chart] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    created_at: datetime


class MessageHistory(BaseModel):
    conversation_id: str
    messages: list[MessageRecord]


class ChatResponse(BaseModel):
    conversation_id: str
    answer: str
    queries: list[QueryTrace] = Field(default_factory=list)
    charts: list[Chart] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)


class ErrorBody(BaseModel):
    code: str
    message: str
