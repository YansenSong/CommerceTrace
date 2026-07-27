from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class EventType(str, Enum):
    CONVERSATION_STARTED = "conversation.started"
    CONTEXT_RETRIEVED = "context.retrieved"
    TOOL_STARTED = "tool.started"
    TOOL_COMPLETED = "tool.completed"
    TOOL_FAILED = "tool.failed"
    EVIDENCE_CREATED = "evidence.created"
    CHART_CREATED = "chart.created"
    ANSWER_DELTA = "answer.delta"
    ANSWER_COMPLETED = "answer.completed"
    REQUEST_FAILED = "request.failed"


class StreamEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    event: EventType
    conversation_id: str
    request_id: str
    timestamp: datetime = Field(default_factory=utc_now)
    payload: dict[str, Any] = Field(default_factory=dict)

    def to_sse(self) -> str:
        data = self.model_dump(mode="json")
        return (
            f"id: {self.event_id}\n"
            f"event: {self.event.value}\n"
            f"data: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"
        )


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4_000)
    conversation_id: str | None = None


class ToolCall(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    arguments: dict[str, Any]


class ToolSchema(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]


class ToolSuccess(BaseModel):
    success: Literal[True] = True
    data: dict[str, Any]


class ToolFailure(BaseModel):
    success: Literal[False] = False
    safe_error_code: str
    safe_error_message: str
    retryable: bool = False


ToolResult = ToolSuccess | ToolFailure


class LlmMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None


class LlmResponse(BaseModel):
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: dict[str, int] = Field(default_factory=dict)


class Evidence(BaseModel):
    evidence_id: str = Field(default_factory=lambda: f"ev_{uuid4().hex[:12]}")
    analysis_step: str
    tool_call_id: str
    claim: str
    sql: str
    columns: list[str]
    row_count: int
    result_hash: str
    execution_time_ms: float = 0
    executed_at: datetime = Field(default_factory=utc_now)
    preview: list[dict[str, Any]] = Field(default_factory=list)


class Chart(BaseModel):
    chart_id: str = Field(default_factory=lambda: f"chart_{uuid4().hex[:12]}")
    evidence_id: str
    chart_type: Literal["metric_card", "bar", "line", "pie"]
    title: str
    figure: dict[str, Any]
