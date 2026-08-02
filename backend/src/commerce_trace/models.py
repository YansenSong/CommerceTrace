from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


class QueryTrace(BaseModel):
    query_id: str
    prepared_query_id: str | None = None
    purpose: str
    sql: str
    plan: list[str] = Field(default_factory=list)
    semantic_fingerprint: str | None = None
    columns: list[str] = Field(default_factory=list)
    row_count: int = 0
    preview: list[dict[str, Any]] = Field(default_factory=list)
    execution_time_ms: float = 0
    truncated: bool = False


class PreparedQuery(BaseModel):
    prepared_query_id: str
    purpose: str
    normalized_sql: str
    plan: list[str] = Field(default_factory=list)
    full_scan_tables: list[str] = Field(default_factory=list)
    semantic_fingerprint: str
    created_at: datetime = Field(default_factory=utc_now)


class QueryResult(BaseModel):
    trace: QueryTrace
    rows: list[dict[str, Any]] = Field(default_factory=list)


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


class KnowledgeConfirm(BaseModel):
    question: str = Field(min_length=1, max_length=4_000)
    sqls: list[str] = Field(min_length=1, max_length=20)
    note: str | None = Field(default=None, max_length=2_000)

    @field_validator("sqls")
    @classmethod
    def _clean_sqls(cls, value: list[str]) -> list[str]:
        cleaned = [sql.strip() for sql in value if sql.strip()]
        if not cleaned:
            raise ValueError("至少需要一条 SQL")
        return cleaned
