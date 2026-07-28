from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Any

import httpx
from openai import AsyncOpenAI

from ..models import LLMMessage, LLMResponse, ToolSchema
from .utils import wrap_message, wrap_tool_schema, unwrap_tool_calls, unwrap_usage

# ── 代理 ───

def _http_proxy_from_environment() -> str | None:
    """读取首个格式有效的 HTTP 代理环境变量。"""

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


# ── 服务抽象与实现 ───────────────────────────────────────────────────────────


class LLMService(ABC):
    """定义 Agent 所依赖的大模型补全服务接口。"""

    @abstractmethod
    async def complete(
        self,
        messages: list[LLMMessage],
        tools: list[ToolSchema],
        system_prompt: str,
    ) -> LLMResponse:
        """根据消息、工具定义和系统提示生成一次模型响应。"""

        raise NotImplementedError


class OpenAICompatibleLLM(LLMService):
    """通过 OpenAI SDK 访问兼容的聊天补全接口。"""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 60,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """保存接口地址、鉴权、模型和网络请求配置。"""

        self.model = model

        client_kwargs: dict[str, Any] = {"timeout": timeout}
        if transport is not None:
            client_kwargs["transport"] = transport
        else:
            proxy = _http_proxy_from_environment()
            if proxy:
                client_kwargs["proxy"] = proxy
            client_kwargs["trust_env"] = False

        http_client = httpx.AsyncClient(**client_kwargs)
        self._client = AsyncOpenAI(
            base_url=base_url.rstrip("/"),
            api_key=api_key,
            http_client=http_client,
        )

    async def complete(
        self,
        messages: list[LLMMessage],
        tools: list[ToolSchema],
        system_prompt: str,
    ) -> LLMResponse:
        """发送聊天补全请求并解析文本、工具调用及令牌用量。"""

        response = await self._client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system_prompt}]
            + [wrap_message(m) for m in messages],
            tools=[wrap_tool_schema(t) for t in tools] or None,
            tool_choice="auto" if tools else None,
            temperature=0,
            extra_body={"thinking": {"type": "disabled"}},
        )

        choice = response.choices[0]
        return LLMResponse(
            content=choice.message.content,
            tool_calls=unwrap_tool_calls(choice.message.tool_calls or []),
            usage=unwrap_usage(response.usage),
        )
