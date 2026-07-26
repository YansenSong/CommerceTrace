from __future__ import annotations

import asyncio
from collections import defaultdict
from copy import deepcopy
from typing import Any, Protocol

from .contracts import Chart, Evidence, StreamEvent, utc_now
from .memory import MemoryRecord, MemoryStatus


class Store(Protocol):
    async def health(self) -> dict[str, Any]: ...

    async def ensure_user(self, user_id: str) -> None: ...

    async def ensure_conversation(self, conversation_id: str, user_id: str, title: str) -> None: ...

    async def save_message(self, conversation_id: str, role: str, content: str) -> None: ...

    async def save_event(self, user_id: str, event: StreamEvent) -> None: ...

    async def save_tool_started(
        self,
        user_id: str,
        conversation_id: str,
        request_id: str,
        tool_call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> None: ...

    async def save_tool_result(
        self,
        user_id: str,
        tool_call_id: str,
        *,
        success: bool,
        summary: dict[str, Any],
    ) -> None: ...

    async def save_evidence(
        self, user_id: str, conversation_id: str, request_id: str, evidence: Evidence
    ) -> None: ...

    async def save_chart(
        self, user_id: str, conversation_id: str, request_id: str, chart: Chart
    ) -> None: ...

    async def list_conversations(
        self, user_id: str, limit: int, offset: int
    ) -> list[dict[str, Any]]: ...

    async def replay_conversation(
        self, user_id: str, conversation_id: str
    ) -> dict[str, Any] | None: ...

    async def upsert_memory(self, record: MemoryRecord) -> MemoryRecord: ...

    async def list_memories(
        self, statuses: set[MemoryStatus] | None = None
    ) -> list[MemoryRecord]: ...

    async def clear_candidates(self) -> int: ...


class InMemoryStore:
    def __init__(self) -> None:
        self.users: set[str] = set()
        self.conversations: dict[str, dict[str, Any]] = {}
        self.messages: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.events: dict[str, list[StreamEvent]] = defaultdict(list)
        self.evidence: dict[str, list[Evidence]] = defaultdict(list)
        self.charts: dict[str, list[Chart]] = defaultdict(list)
        self.tool_calls: dict[str, dict[str, Any]] = {}
        self.tool_results: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.memories: dict[str, MemoryRecord] = {}
        self._memory_dedupe: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def health(self) -> dict[str, Any]:
        return {"database": "ready", "backend": "ready"}

    async def ensure_user(self, user_id: str) -> None:
        self.users.add(user_id)

    async def ensure_conversation(self, conversation_id: str, user_id: str, title: str) -> None:
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
        self.messages[conversation_id].append(
            {"role": role, "content": content, "created_at": utc_now()}
        )
        self.conversations[conversation_id]["updated_at"] = utc_now()

    async def save_event(self, user_id: str, event: StreamEvent) -> None:
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
        if self.conversations.get(conversation_id, {}).get("user_id") != user_id:
            raise PermissionError("conversation_not_found")
        self.evidence[conversation_id].append(deepcopy(evidence))

    async def save_chart(
        self, user_id: str, conversation_id: str, request_id: str, chart: Chart
    ) -> None:
        if self.conversations.get(conversation_id, {}).get("user_id") != user_id:
            raise PermissionError("conversation_not_found")
        self.charts[conversation_id].append(deepcopy(chart))

    async def list_conversations(
        self, user_id: str, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        items = [
            deepcopy(item) for item in self.conversations.values() if item["user_id"] == user_id
        ]
        items.sort(key=lambda item: item["updated_at"], reverse=True)
        return items[offset : offset + limit]

    async def replay_conversation(
        self, user_id: str, conversation_id: str
    ) -> dict[str, Any] | None:
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

    async def upsert_memory(self, record: MemoryRecord) -> MemoryRecord:
        async with self._lock:
            existing_id = self._memory_dedupe.get(record.dedupe_key)
            if existing_id and existing_id != record.memory_id:
                existing = self.memories[existing_id]
                existing.last_verified_at = record.last_verified_at or utc_now()
                if record.status is not MemoryStatus.CANDIDATE:
                    existing.status = record.status
                return deepcopy(existing)
            self.memories[record.memory_id] = deepcopy(record)
            self._memory_dedupe[record.dedupe_key] = record.memory_id
            return deepcopy(record)

    async def list_memories(self, statuses: set[MemoryStatus] | None = None) -> list[MemoryRecord]:
        records = list(self.memories.values())
        if statuses is not None:
            records = [record for record in records if record.status in statuses]
        return deepcopy(records)

    async def clear_candidates(self) -> int:
        candidate_ids = [
            memory_id
            for memory_id, record in self.memories.items()
            if record.status is MemoryStatus.CANDIDATE
        ]
        for memory_id in candidate_ids:
            record = self.memories.pop(memory_id)
            self._memory_dedupe.pop(record.dedupe_key, None)
        return len(candidate_ids)
