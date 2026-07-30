from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import SummarizationMiddleware
from langchain_core.callbacks import UsageMetadataCallbackHandler
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver

from ..config import Config
from ..models import ChatResponse, Usage
from .prompt import SYSTEM_PROMPT
from .sql_safety import SqlSafetyPolicy
from .tools import (
    AgentContext,
    RunArtifacts,
    get_schema,
    run_sql,
    visualize_data,
)


class Agent:
    def __init__(
        self,
        *,
        config: Config,
        model: BaseChatModel,
        checkpointer: BaseCheckpointSaver[Any],
    ) -> None:
        self._database_path = config.database_path.resolve()
        self._statement_timeout_ms = config.statement_timeout_ms
        self._sql_policy = SqlSafetyPolicy(
            max_rows=config.max_result_rows,
            max_distinct_values=config.max_distinct_values,
        )

        middleware: list[Any] = [
            SummarizationMiddleware(
                model,
                trigger=("messages", 30),
                keep=("messages", 12),
            ),
        ]

        self._agent = create_agent(
            model,
            tools=[get_schema, run_sql, visualize_data],
            system_prompt=SYSTEM_PROMPT,
            middleware=middleware,
            context_schema=AgentContext,
            checkpointer=checkpointer,
            name="commerce_trace",
        )

    async def invoke(
        self,
        *,
        conversation_id: str,
        thread_id: str,
        message: str,
    ) -> ChatResponse:
        
        artifacts = RunArtifacts()
        usage_callback = UsageMetadataCallbackHandler()
        config: RunnableConfig = {
            "configurable": {"thread_id": thread_id},
            "callbacks": [usage_callback],
        }

        result = await self._agent.ainvoke(
            {"messages": [{"role": "user", "content": message}]},
            config=config,
            context=AgentContext(
                artifacts=artifacts,
                database_path=self._database_path,
                statement_timeout_ms=self._statement_timeout_ms,
                sql_policy=self._sql_policy,
            ),
        )
        
        answer = _last_answer(result.get("messages", []))
        usage = _usage(usage_callback.usage_metadata)
        return ChatResponse(
            conversation_id=conversation_id,
            answer=answer,
            queries=artifacts.queries,
            charts=artifacts.charts,
            usage=usage,
        )


def _last_answer(messages: list[Any]) -> str:
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            content = message.content
            if isinstance(content, str) and content.strip():
                return content
            if isinstance(content, list):
                parts = [
                    str(block.get("text", ""))
                    for block in content
                    if isinstance(block, dict) and block.get("type") == "text"
                ]
                if any(part.strip() for part in parts):
                    return "\n".join(parts)
    return "本次分析未能生成最终回答，请调整问题后重试。"


def _usage(metadata: Mapping[str, Mapping[str, Any]]) -> Usage:
    input_tokens = 0
    output_tokens = 0
    for item in metadata.values():
        input_tokens += int(item.get("input_tokens", 0))
        output_tokens += int(item.get("output_tokens", 0))
    return Usage(input_tokens=input_tokens, output_tokens=output_tokens)
