"""Injects confirmed-query few-shot examples into the model request."""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import AIMessage, HumanMessage

from .tools.context import AgentContext


class FewShotMemoryMiddleware(AgentMiddleware[Any, AgentContext]):
    """Prepends recalled confirmed-query examples to the first model call.

    Examples are read from ``AgentContext.few_shot`` and applied through
    ``request.override(messages=...)``, so the injection reaches the model without
    ever mutating the checkpointed conversation state. The static system prompt is
    still prepended ahead of the examples by the agent factory.
    """

    async def awrap_model_call(
        self,
        request: ModelRequest[AgentContext],
        handler: Any,
    ) -> Any:
        context = request.runtime.context
        examples = context.few_shot if context is not None else None
        messages = request.messages
        if not examples or not messages or not isinstance(messages[-1], HumanMessage):
            return await handler(request)
        injected: list[Any] = []
        for example in examples:
            injected.append(
                HumanMessage(
                    content=(
                        "【历史已确认问答，可参考其中的 SQL 与分析思路；"
                        "仍需按当前问题核实口径，禁止照抄未执行的数字】\n"
                        f"{example.get('question', '')}"
                    )
                )
            )
            injected.append(AIMessage(content=_format_sqls(example.get("sqls") or [])))
        return await handler(request.override(messages=[*injected, *messages]))


def _format_sqls(sqls: list[Any]) -> str:
    if not sqls:
        return "（无复用 SQL）"
    return "\n\n".join(f"```sql\n{sql}\n```" for sql in sqls)
