from __future__ import annotations

import json

from .core import Agent
from .tool import FakeSqlExecutor, build_default_registry
from .context import ContextAssembler
from ..models import LlmMessage, LlmResponse, ToolCall, ToolSchema
from .llm import LlmService
from ..persistence import InMemoryStore


class ScriptedLlm(LlmService):
    """Deterministic tool-calling test double; never used by the application runtime."""

    async def complete(
        self,
        messages: list[LlmMessage],
        tools: list[ToolSchema],
        system_prompt: str,
    ) -> LlmResponse:
        tool_messages = [message for message in messages if message.role == "tool"]
        sql_messages = [
            message for message in tool_messages if '"tool_name": "run_sql"' in message.content
        ]
        if sql_messages:
            last_result = json.loads(sql_messages[-1].content)
            if not last_result.get("success") and last_result.get("retryable"):
                previous_call = next(
                    (
                        call
                        for message in reversed(messages)
                        if message.role == "assistant"
                        for call in message.tool_calls
                        if call.name == "run_sql"
                    ),
                    None,
                )
                if previous_call is not None:
                    return LlmResponse(
                        tool_calls=[
                            ToolCall(
                                name=previous_call.name,
                                arguments=previous_call.arguments,
                            )
                        ]
                    )
            return LlmResponse(content="已根据工具结果完成分析。")

        return LlmResponse(
            tool_calls=[
                ToolCall(
                    name="run_sql",
                    arguments={
                        "sql": (
                            "SELECT SUM(total_amount) AS revenue FROM ecommerce.orders "
                            "WHERE status IN ('paid','completed')"
                        ),
                        "purpose": "统计销售额",
                        "expected_columns": ["revenue"],
                    },
                )
            ]
        )


def build_test_agent(store: InMemoryStore) -> Agent:
    executor = FakeSqlExecutor(
        rows=[
            {"region": "华东", "revenue": 1200.0},
            {"region": "华南", "revenue": 900.0},
        ]
    )
    return Agent(
        llm=ScriptedLlm(),
        registry=build_default_registry(executor=executor),
        context_assembler=ContextAssembler(),
        store=store,
    )
