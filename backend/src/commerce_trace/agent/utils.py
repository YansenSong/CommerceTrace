"""(Domain ⇄ OpenAI API) 格式转换。"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from ..models import LLMMessage, ToolCall, ToolSchema


def wrap_message(msg: LLMMessage) -> dict[str, Any]:
    item: dict[str, Any] = {"role": msg.role, "content": msg.content}
    if msg.tool_call_id:
        item["tool_call_id"] = msg.tool_call_id
    if msg.tool_calls:
        item["tool_calls"] = [wrap_tool_call(c) for c in msg.tool_calls]
    return item


def wrap_tool_call(call: ToolCall) -> dict[str, Any]:
    return {
        "id": call.id,
        "type": "function",
        "function": {
            "name": call.name,
            "arguments": json.dumps(call.arguments, ensure_ascii=False),
        },
    }


def wrap_tool_schema(tool: ToolSchema) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        },
    }


def unwrap_tool_calls(raw_calls: list[Any]) -> list[ToolCall]:
    result: list[ToolCall] = []
    for raw in raw_calls:
        result.append(
            ToolCall(
                id=raw.id or str(uuid4()),
                name=raw.function.name,
                arguments=json.loads(raw.function.arguments or "{}"),
            )
        )
    return result


def unwrap_usage(usage: Any) -> dict[str, int]:
    if not usage:
        return {}
    return {k: int(v) for k, v in usage.model_dump().items() if isinstance(v, int)}
