from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

from .context import ContextAssembler
from ..models import (
    EventType,
    LlmMessage,
    StreamEvent,
    ToolFailure,
    ToolSuccess,
)
from .llm import LlmService
from ..persistence import ConversationLedger
from .state import RequestPhase, RequestState
from .synthesis import synthesize
from .tool import ToolRegistry
from .prompt import SYSTEM_PROMPT


class Agent:
    """编排模型、工具、状态机和持久化以完成一次数据问答。"""

    def __init__(
        self,
        *,
        llm: LlmService,
        registry: ToolRegistry,
        context_assembler: ContextAssembler,
        store: ConversationLedger,
        max_tool_iterations: int = 10,
        max_business_sql_calls: int = 5,
        max_sql_retries_per_purpose: int = 2,
        enable_sql_retries: bool = True,
    ) -> None:
        """注入 Agent 依赖，并设置工具调用与 SQL 重试预算。"""

        self.llm = llm
        self.registry = registry
        self.context_assembler = context_assembler
        self.store = store
        self.max_tool_iterations = max_tool_iterations
        self.max_business_sql_calls = max_business_sql_calls
        self.max_sql_retries_per_purpose = max_sql_retries_per_purpose
        self.enable_sql_retries = enable_sql_retries

    async def _make_event(
        self,
        *,
        user_id: str,
        conversation_id: str,
        request_id: str,
        event: EventType,
        payload: dict[str, Any],
    ) -> StreamEvent:
        """创建、持久化并返回一个流式事件。"""

        item = StreamEvent(
            event=event,
            conversation_id=conversation_id,
            request_id=request_id,
            payload=payload,
        )
        await self.store.save_event(user_id, item)
        return item

    async def run(
        self,
        *,
        user_id: str,
        conversation_id: str,
        request_id: str,
        question: str,
    ) -> AsyncGenerator[StreamEvent, None]:
        """执行完整问答流程，并按发生顺序产出流式事件。"""

        state = RequestState(
            user_id=user_id,
            conversation_id=conversation_id,
            request_id=request_id,
            question=question,
        )
        await self.store.ensure_user(user_id)
        await self.store.ensure_conversation(conversation_id, user_id, question)
        await self.store.save_message(conversation_id, "user", question)
        yield await self._make_event(
            user_id=user_id,
            conversation_id=conversation_id,
            request_id=request_id,
            event=EventType.CONVERSATION_STARTED,
            payload={"question": question},
        )

        if self._is_unsafe_request(question):
            answer = (
                "该请求涉及写入、越权或敏感系统信息，CommerceTrace 只允许读取 ecommerce 业务数据。"
            )
            async for event in self._complete(
                state,
                answer,
                RequestPhase.REFUSED,
                {
                    "answer": answer,
                    "evidence_ids": [],
                    "status": "refused",
                    "safe_error_code": "unsafe_request",
                },
            ):
                yield event
            return

        try:
            context = await self.context_assembler.assemble()
        except Exception:
            state.finish(RequestPhase.FAILED)
            yield await self._make_event(
                user_id=user_id,
                conversation_id=conversation_id,
                request_id=request_id,
                event=EventType.REQUEST_FAILED,
                payload={
                    "safe_error_code": "context_unavailable",
                    "message": "核心 Schema 上下文加载失败",
                },
            )
            return
        state.mark_context_ready()
        yield await self._make_event(
            user_id=user_id,
            conversation_id=conversation_id,
            request_id=request_id,
            event=EventType.CONTEXT_ASSEMBLED,
            payload={
                "schema_version": context.schema_version,
                "schema_fingerprint": context.schema_fingerprint,
                "knowledge_version": context.knowledge_version,
                "degraded": context.degraded,
            },
        )

        state.prepare_execution()
        assert state.tool_context is not None
        state.tool_context.store = self.store

        while state.tool_iterations < self.max_tool_iterations:
            response = await self.llm.complete(
                state.messages,
                self.registry.schemas(),
                SYSTEM_PROMPT + "\n\n" + context.prompt_section(),
            )
            state.record_llm_usage(response.usage)
            if not response.tool_calls:
                state.llm_content = response.content or ""
                break
            state.messages.append(
                LlmMessage(
                    role="assistant",
                    content=response.content or "",
                    tool_calls=response.tool_calls,
                )
            )
            for call in response.tool_calls:
                purpose = str(call.arguments.get("purpose", "未声明目的"))
                tool_kind = self.registry.tool_kind(call.name)
                if not state.begin_tool(
                    name=call.name,
                    kind=tool_kind,
                    purpose=purpose,
                    max_tool_iterations=self.max_tool_iterations,
                    max_business_sql_calls=self.max_business_sql_calls,
                    max_sql_retries_per_purpose=self.max_sql_retries_per_purpose,
                ):
                    break
                safe_arguments = self._safe_arguments(call.name, call.arguments)
                await self.store.save_tool_started(
                    user_id,
                    conversation_id,
                    request_id,
                    call.id,
                    call.name,
                    safe_arguments,
                )
                yield await self._make_event(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    request_id=request_id,
                    event=EventType.TOOL_STARTED,
                    payload={
                        "tool_call_id": call.id,
                        "tool_name": call.name,
                        "arguments": safe_arguments,
                    },
                )
                assert state.tool_context is not None
                state.tool_context.tool_call_id = call.id
                result = await self.registry.execute(call.name, call.arguments, state.tool_context)
                if isinstance(result, ToolFailure):
                    state.record_tool_failure(tool_kind, purpose)
                    await self.store.save_tool_result(
                        user_id,
                        call.id,
                        success=False,
                        summary=result.model_dump(),
                    )
                    yield await self._make_event(
                        user_id=user_id,
                        conversation_id=conversation_id,
                        request_id=request_id,
                        event=EventType.TOOL_FAILED,
                        payload={
                            "tool_call_id": call.id,
                            "tool_name": call.name,
                            **result.model_dump(),
                        },
                    )
                    state.messages.append(
                        LlmMessage(
                            role="tool",
                            tool_call_id=call.id,
                            content=json.dumps(
                                {
                                    "tool_name": call.name,
                                    "success": False,
                                    **result.model_dump(),
                                },
                                ensure_ascii=False,
                            ),
                        )
                    )
                    if tool_kind == "business_sql" and not self.enable_sql_retries:
                        state.incomplete_reason = "sql_retry_disabled"
                        break
                    continue

                assert isinstance(result, ToolSuccess)
                await self.store.save_tool_result(
                    user_id,
                    call.id,
                    success=True,
                    summary={"data": result.data},
                )
                yield await self._make_event(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    request_id=request_id,
                    event=EventType.TOOL_COMPLETED,
                    payload={
                        "tool_call_id": call.id,
                        "tool_name": call.name,
                        "data": result.data,
                    },
                )
                # Lifecycle hooks (on_success) already persisted results to store
                # and pushed Evidence / Chart objects into context lists.
                for evidence in state.tool_context.created_evidence:
                    state.add_evidence(evidence)
                    yield await self._make_event(
                        user_id=user_id,
                        conversation_id=conversation_id,
                        request_id=request_id,
                        event=EventType.EVIDENCE_CREATED,
                        payload=evidence.model_dump(mode="json"),
                    )
                state.tool_context.created_evidence.clear()
                for chart in state.tool_context.created_charts:
                    yield await self._make_event(
                        user_id=user_id,
                        conversation_id=conversation_id,
                        request_id=request_id,
                        event=EventType.CHART_CREATED,
                        payload=chart.model_dump(mode="json"),
                    )
                state.tool_context.created_charts.clear()
                state.messages.append(
                    LlmMessage(
                        role="tool",
                        tool_call_id=call.id,
                        content=json.dumps(
                            {
                                "tool_name": call.name,
                                "success": True,
                                "data": result.data,
                            },
                            ensure_ascii=False,
                            default=str,
                        ),
                    )
                )
            if state.incomplete_reason:
                break

        state.begin_synthesis()
        if state.tool_iterations >= self.max_tool_iterations and not state.incomplete_reason:
            state.incomplete_reason = "tool_iteration_limit"
        if not state.evidence and not state.incomplete_reason:
            state.incomplete_reason = "insufficient_evidence"
        answer = synthesize(
            state.evidence,
            state.llm_content,
            state.incomplete_reason,
        )
        terminal_phase = (
            RequestPhase.INCOMPLETE if state.incomplete_reason else RequestPhase.COMPLETED
        )
        async for event in self._complete(
            state,
            answer,
            terminal_phase,
            {
                "answer": answer,
                "evidence_ids": [item.evidence_id for item in state.evidence],
                "status": "partial" if state.incomplete_reason else "completed",
                "stop_reason": state.incomplete_reason,
                "usage": state.usage(),
            },
        ):
            yield event

    async def _complete(
        self,
        state: RequestState,
        answer: str,
        terminal_phase: RequestPhase,
        payload: dict[str, Any],
    ) -> AsyncGenerator[StreamEvent, None]:
        """结束请求、保存回答，并依次产出回答增量和完成事件。"""

        state.finish(terminal_phase)
        yield await self._make_event(
            user_id=state.user_id,
            conversation_id=state.conversation_id,
            request_id=state.request_id,
            event=EventType.ANSWER_DELTA,
            payload={"delta": answer},
        )
        await self.store.save_message(state.conversation_id, "assistant", answer)
        yield await self._make_event(
            user_id=state.user_id,
            conversation_id=state.conversation_id,
            request_id=state.request_id,
            event=EventType.ANSWER_COMPLETED,
            payload=payload,
        )

    @staticmethod
    def _is_unsafe_request(question: str) -> bool:
        """检查问题是否包含写入、越权或敏感信息请求标记。"""

        lowered = question.lower()
        markers = {
            "drop ",
            "delete ",
            "update ",
            "insert ",
            "truncate ",
            "create ",
            "alter ",
            "grant ",
            "revoke ",
            "copy ",
            "merge ",
            "agent_app",
            "连接字符串",
            "数据库密码",
            "系统提示词",
        }
        return any(marker in lowered for marker in markers)

    @staticmethod
    def _safe_arguments(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """筛选允许持久化和发送给前端的安全工具参数。"""

        if name != "run_sql":
            return arguments
        return {
            "sql": arguments.get("sql"),
            "purpose": arguments.get("purpose"),
            "expected_columns": arguments.get("expected_columns", []),
        }
