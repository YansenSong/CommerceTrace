from __future__ import annotations

import json

from .core import Agent
from .tool import FakeSqlExecutor, build_default_registry
from .context import ContextAssembler
from ..models import LLMMessage, LLMResponse, ToolCall, ToolSchema
from .llm import LLMService
from ..persistence import InMemoryStore


class ScriptedLLM(LLMService):
    """提供确定性工具调用的测试替身，不用于正式应用运行时。"""

    async def complete(
        self,
        messages: list[LLMMessage],
        tools: list[ToolSchema],
        system_prompt: str,
    ) -> LLMResponse:
        """根据已有工具消息返回预设的 SQL 调用、重试或最终回答。"""

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
                    return LLMResponse(
                        tool_calls=[
                            ToolCall(
                                name=previous_call.name,
                                arguments=previous_call.arguments,
                            )
                        ]
                    )
            return LLMResponse(content="已根据工具结果完成分析。")

        return LLMResponse(
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
    """使用内存存储和固定数据构建无需联网的测试 Agent。"""

    executor = FakeSqlExecutor(
        rows=[
            {"region": "华东", "revenue": 1200.0},
            {"region": "华南", "revenue": 900.0},
        ]
    )
    return Agent(
        llm=ScriptedLLM(),
        registry=build_default_registry(executor=executor),
        context_assembler=ContextAssembler(),
        store=store,
    )
