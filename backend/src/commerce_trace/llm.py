from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from typing import Any
from uuid import uuid4

import httpx

from .contracts import LlmMessage, LlmResponse, ToolCall, ToolSchema


def _http_proxy_from_environment() -> str | None:
    for variable in (
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "ALL_PROXY",
        "https_proxy",
        "http_proxy",
        "all_proxy",
    ):
        value = os.environ.get(variable)
        if value and value.startswith(("http://", "https://")):
            return value
    return None


class LlmService(ABC):
    @abstractmethod
    async def complete(
        self,
        messages: list[LlmMessage],
        tools: list[ToolSchema],
        system_prompt: str,
    ) -> LlmResponse:
        raise NotImplementedError


class OpenAICompatibleLlm(LlmService):
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 60,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.transport = transport

    async def complete(
        self,
        messages: list[LlmMessage],
        tools: list[ToolSchema],
        system_prompt: str,
    ) -> LlmResponse:
        payload_messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        for message in messages:
            item: dict[str, Any] = {"role": message.role, "content": message.content}
            if message.tool_call_id:
                item["tool_call_id"] = message.tool_call_id
            if message.tool_calls:
                item["tool_calls"] = [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(call.arguments, ensure_ascii=False),
                        },
                    }
                    for call in message.tool_calls
                ]
            payload_messages.append(item)
        payload = {
            "model": self.model,
            "messages": payload_messages,
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    },
                }
                for tool in tools
            ],
            "tool_choice": "auto",
            "temperature": 0,
            "thinking": {"type": "disabled"},
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        proxy = None if self.transport is not None else _http_proxy_from_environment()
        async with httpx.AsyncClient(
            timeout=self.timeout,
            transport=self.transport,
            proxy=proxy,
            trust_env=False,
        ) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
        body = response.json()
        choice = body["choices"][0]["message"]
        calls: list[ToolCall] = []
        for raw in choice.get("tool_calls") or []:
            function = raw["function"]
            calls.append(
                ToolCall(
                    id=raw.get("id") or str(uuid4()),
                    name=function["name"],
                    arguments=json.loads(function.get("arguments") or "{}"),
                )
            )
        usage = {
            key: int(value)
            for key, value in (body.get("usage") or {}).items()
            if isinstance(value, int)
        }
        return LlmResponse(
            content=choice.get("content"),
            tool_calls=calls,
            usage=usage,
        )
