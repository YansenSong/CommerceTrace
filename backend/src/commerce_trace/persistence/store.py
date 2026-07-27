from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from typing import Any, Protocol

from ..models import Chart, Evidence, StreamEvent, utc_now


class ConversationLedger(Protocol):
    """定义 Agent 记录和回放会话所需的持久化接口。"""

    async def health(self) -> dict[str, Any]:
        """返回存储及其依赖的健康状态。"""

        ...

    async def ensure_user(self, user_id: str) -> None:
        """确保指定用户已存在。"""

        ...

    async def ensure_conversation(self, conversation_id: str, user_id: str, title: str) -> None:
        """确保会话存在且归属于指定用户。"""

        ...

    async def save_message(self, conversation_id: str, role: str, content: str) -> None:
        """保存一条会话消息。"""

        ...

    async def save_event(self, user_id: str, event: StreamEvent) -> None:
        """保存一条归属于指定用户的流式事件。"""

        ...

    async def save_tool_started(
        self,
        user_id: str,
        conversation_id: str,
        request_id: str,
        tool_call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> None:
        """记录一次工具调用已经开始。"""

        ...

    async def save_tool_result(
        self,
        user_id: str,
        tool_call_id: str,
        *,
        success: bool,
        summary: dict[str, Any],
    ) -> None:
        """保存一次工具调用的成功状态和结果摘要。"""

        ...

    async def save_evidence(
        self, user_id: str, conversation_id: str, request_id: str, evidence: Evidence
    ) -> None:
        """保存一次请求生成的证据。"""

        ...

    async def save_chart(
        self, user_id: str, conversation_id: str, request_id: str, chart: Chart
    ) -> None:
        """保存一次请求生成的图表。"""

        ...

    async def list_conversations(
        self, user_id: str, limit: int, offset: int
    ) -> list[dict[str, Any]]:
        """分页列出指定用户的会话。"""

        ...

    async def replay_conversation(
        self, user_id: str, conversation_id: str
    ) -> dict[str, Any] | None:
        """读取并组装指定用户的完整会话记录。"""

        ...


class InMemoryStore:
    """以内存数据结构实现会话账本，主要用于测试和降级运行。"""

    def __init__(self) -> None:
        """初始化用户、会话、消息、事件和工具结果容器。"""

        self.users: set[str] = set()
        self.conversations: dict[str, dict[str, Any]] = {}
        self.messages: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.events: dict[str, list[StreamEvent]] = defaultdict(list)
        self.evidence: dict[str, list[Evidence]] = defaultdict(list)
        self.charts: dict[str, list[Chart]] = defaultdict(list)
        self.tool_calls: dict[str, dict[str, Any]] = {}
        self.tool_results: dict[str, list[dict[str, Any]]] = defaultdict(list)

    async def health(self) -> dict[str, Any]:
        """返回内存存储始终就绪的健康状态。"""

        return {"database": "ready", "backend": "ready"}

    async def ensure_user(self, user_id: str) -> None:
        """将用户编号加入内存用户集合。"""

        self.users.add(user_id)

    async def ensure_conversation(self, conversation_id: str, user_id: str, title: str) -> None:
        """创建会话，或验证已有会话是否属于指定用户。"""

        current = self.conversations.get(conversation_id)
        if current and current["user_id"] != user_id:
            raise PermissionError("conversation_not_found")
        if not current:
            self.conversations[conversation_id] = {
                "conversation_id": conversation_id,
                "user_id": user_id,
                "title": title[:120],
                "created_at": utc_now(),
                "updated_at": utc_now(),
            }

    async def save_message(self, conversation_id: str, role: str, content: str) -> None:
        """追加会话消息并刷新会话更新时间。"""

        self.messages[conversation_id].append(
            {"role": role, "content": content, "created_at": utc_now()}
        )
        self.conversations[conversation_id]["updated_at"] = utc_now()

    async def save_event(self, user_id: str, event: StreamEvent) -> None:
        """验证会话归属后保存流式事件副本。"""

        owner = self.conversations.get(event.conversation_id, {}).get("user_id")
        if owner != user_id:
            raise PermissionError("conversation_not_found")
        self.events[event.conversation_id].append(deepcopy(event))

    async def save_tool_started(
        self,
        user_id: str,
        conversation_id: str,
        request_id: str,
        tool_call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> None:
        """验证会话归属后记录工具调用参数和开始状态。"""

        if self.conversations.get(conversation_id, {}).get("user_id") != user_id:
            raise PermissionError("conversation_not_found")
        self.tool_calls[tool_call_id] = {
            "tool_call_id": tool_call_id,
            "conversation_id": conversation_id,
            "request_id": request_id,
            "tool_name": tool_name,
            "arguments": deepcopy(arguments),
            "status": "started",
            "created_at": utc_now(),
        }

    async def save_tool_result(
        self,
        user_id: str,
        tool_call_id: str,
        *,
        success: bool,
        summary: dict[str, Any],
    ) -> None:
        """更新工具调用状态并保存结果摘要副本。"""

        call = self.tool_calls.get(tool_call_id)
        if call is None or self.conversations[call["conversation_id"]]["user_id"] != user_id:
            raise PermissionError("tool_call_not_found")
        call["status"] = "completed" if success else "failed"
        self.tool_results[tool_call_id].append(
            {
                "tool_call_id": tool_call_id,
                "success": success,
                "result_summary": deepcopy(summary),
                "created_at": utc_now(),
            }
        )

    async def save_evidence(
        self, user_id: str, conversation_id: str, request_id: str, evidence: Evidence
    ) -> None:
        """验证会话归属后保存证据副本。"""

        if self.conversations.get(conversation_id, {}).get("user_id") != user_id:
            raise PermissionError("conversation_not_found")
        self.evidence[conversation_id].append(deepcopy(evidence))

    async def save_chart(
        self, user_id: str, conversation_id: str, request_id: str, chart: Chart
    ) -> None:
        """验证会话归属后保存图表副本。"""

        if self.conversations.get(conversation_id, {}).get("user_id") != user_id:
            raise PermissionError("conversation_not_found")
        self.charts[conversation_id].append(deepcopy(chart))

    async def list_conversations(
        self, user_id: str, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        """按更新时间倒序分页返回指定用户的会话副本。"""

        items = [
            deepcopy(item) for item in self.conversations.values() if item["user_id"] == user_id
        ]
        items.sort(key=lambda item: item["updated_at"], reverse=True)
        return items[offset : offset + limit]

    async def replay_conversation(
        self, user_id: str, conversation_id: str
    ) -> dict[str, Any] | None:
        """组装并返回指定用户会话的消息、事件、工具、证据和图表。"""

        conversation = self.conversations.get(conversation_id)
        if not conversation or conversation["user_id"] != user_id:
            return None
        return {
            "conversation": deepcopy(conversation),
            "messages": deepcopy(self.messages[conversation_id]),
            "events": [event.model_dump(mode="json") for event in self.events[conversation_id]],
            "tool_calls": [
                deepcopy(call)
                for call in self.tool_calls.values()
                if call["conversation_id"] == conversation_id
            ],
            "tool_results": [
                deepcopy(result)
                for call in self.tool_calls.values()
                if call["conversation_id"] == conversation_id
                for result in self.tool_results[call["tool_call_id"]]
            ],
            "evidence": [item.model_dump(mode="json") for item in self.evidence[conversation_id]],
            "charts": [item.model_dump(mode="json") for item in self.charts[conversation_id]],
        }
