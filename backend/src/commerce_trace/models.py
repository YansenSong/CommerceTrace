from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    """返回带 UTC 时区信息的当前时间。"""

    return datetime.now(timezone.utc)


class EventType(str, Enum):
    """定义服务端流式事件的类型。"""

    CONVERSATION_STARTED = "conversation.started"
    CONTEXT_ASSEMBLED = "context.assembled"
    TOOL_STARTED = "tool.started"
    TOOL_COMPLETED = "tool.completed"
    TOOL_FAILED = "tool.failed"
    EVIDENCE_CREATED = "evidence.created"
    CHART_CREATED = "chart.created"
    ANSWER_DELTA = "answer.delta"
    ANSWER_COMPLETED = "answer.completed"
    REQUEST_FAILED = "request.failed"


class StreamEvent(BaseModel):
    """表示一次通过 SSE 发送并可持久化的流式事件。"""

    event_id: str = Field(default_factory=lambda: str(uuid4()))
    event: EventType
    conversation_id: str
    request_id: str
    timestamp: datetime = Field(default_factory=utc_now)
    payload: dict[str, Any] = Field(default_factory=dict)

    def to_sse(self) -> str:
        """将事件序列化为符合 SSE 协议的文本消息。"""

        data = self.model_dump(mode="json")
        return (
            f"id: {self.event_id}\n"
            f"event: {self.event.value}\n"
            f"data: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"
        )


class ChatRequest(BaseModel):
    """描述聊天接口接收的用户问题及可选会话编号。"""

    question: str = Field(min_length=1, max_length=4_000)
    conversation_id: str | None = None


class ToolCall(BaseModel):
    """描述大模型请求调用某个工具时的名称和参数。"""

    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    arguments: dict[str, Any]


class ToolSchema(BaseModel):
    """描述提供给大模型的工具名称、用途和参数结构。"""

    name: str
    description: str
    parameters: dict[str, Any]


class ToolSuccess(BaseModel):
    """表示工具成功执行后返回的数据。"""

    success: Literal[True] = True
    data: dict[str, Any]


class ToolFailure(BaseModel):
    """表示可安全暴露给调用方的工具执行失败信息。"""

    success: Literal[False] = False
    safe_error_code: str
    safe_error_message: str
    retryable: bool = False


ToolResult = ToolSuccess | ToolFailure


class LlmMessage(BaseModel):
    """表示发送给大模型的一条对话或工具消息。"""

    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None


class LlmResponse(BaseModel):
    """表示大模型返回的文本、工具调用和用量信息。"""

    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: dict[str, int] = Field(default_factory=dict)


class Evidence(BaseModel):
    """记录由查询产生、可追溯且可引用的分析证据。"""

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
    """描述基于某条证据生成的受控图表。"""

    chart_id: str = Field(default_factory=lambda: f"chart_{uuid4().hex[:12]}")
    evidence_id: str
    chart_type: Literal["metric_card", "bar", "line", "pie"]
    title: str
    figure: dict[str, Any]
