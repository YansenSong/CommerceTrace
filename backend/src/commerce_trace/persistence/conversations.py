from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import aiosqlite

from ..models import (
    Chart,
    ConversationCreate,
    ConversationSummary,
    MessageRecord,
    QueryTrace,
    Usage,
    utc_now,
)


class ConversationStore:
    """Small product-facing catalog beside LangGraph's checkpoint tables."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._write_lock = asyncio.Lock()

    async def setup(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as connection:
            await connection.execute("PRAGMA journal_mode = WAL")
            await connection.execute("PRAGMA foreign_keys = ON")
            await connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conversations (
                    conversation_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS conversations_user_updated_idx
                    ON conversations (user_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS messages (
                    message_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL
                        REFERENCES conversations(conversation_id) ON DELETE CASCADE,
                    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    queries_json TEXT NOT NULL DEFAULT '[]',
                    charts_json TEXT NOT NULL DEFAULT '[]',
                    usage_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                """
            )
            await connection.commit()

    async def health(self) -> bool:
        try:
            async with aiosqlite.connect(self.path) as connection:
                await connection.execute("SELECT 1")
            return True
        except aiosqlite.Error:
            return False

    async def create(self, user_id: str) -> ConversationCreate:
        conversation_id = f"conv_{uuid4().hex}"
        now = utc_now()
        async with self._write_lock, aiosqlite.connect(self.path) as connection:
            await connection.execute("PRAGMA foreign_keys = ON")
            await connection.execute(
                "INSERT OR IGNORE INTO users (user_id, created_at) VALUES (?, ?)",
                (user_id, now.isoformat()),
            )
            await connection.execute(
                """
                INSERT INTO conversations
                    (conversation_id, user_id, title, created_at, updated_at)
                VALUES (?, ?, '新会话', ?, ?)
                """,
                (conversation_id, user_id, now.isoformat(), now.isoformat()),
            )
            await connection.commit()
        return ConversationCreate(
            conversation_id=conversation_id,
            title="新会话",
            created_at=now,
            updated_at=now,
        )

    async def list_conversations(
        self,
        user_id: str,
        *,
        limit: int,
        offset: int,
    ) -> list[ConversationSummary]:
        async with aiosqlite.connect(self.path) as connection:
            connection.row_factory = aiosqlite.Row
            cursor = await connection.execute(
                """
                SELECT conversation_id, title, created_at, updated_at
                FROM conversations
                WHERE user_id = ?
                ORDER BY updated_at DESC
                LIMIT ? OFFSET ?
                """,
                (user_id, limit, offset),
            )
            rows = await cursor.fetchall()
        return [ConversationSummary.model_validate(dict(row)) for row in rows]

    async def owns(self, user_id: str, conversation_id: str) -> bool:
        async with aiosqlite.connect(self.path) as connection:
            cursor = await connection.execute(
                """
                SELECT 1 FROM conversations
                WHERE conversation_id = ? AND user_id = ?
                """,
                (conversation_id, user_id),
            )
            return await cursor.fetchone() is not None

    async def add_message(
        self,
        conversation_id: str,
        *,
        role: Literal["user", "assistant"],
        content: str,
        queries: list[QueryTrace] | None = None,
        charts: list[Chart] | None = None,
        usage: Usage | None = None,
    ) -> None:
        now = utc_now().isoformat()
        query_payload = [item.model_dump(mode="json") for item in queries or []]
        chart_payload = [item.model_dump(mode="json") for item in charts or []]
        usage_payload = (usage or Usage()).model_dump(mode="json")
        async with self._write_lock, aiosqlite.connect(self.path) as connection:
            await connection.execute("PRAGMA foreign_keys = ON")
            await connection.execute(
                """
                INSERT INTO messages
                    (conversation_id, role, content, queries_json, charts_json,
                     usage_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conversation_id,
                    role,
                    content,
                    json.dumps(query_payload, ensure_ascii=False, default=str),
                    json.dumps(chart_payload, ensure_ascii=False, default=str),
                    json.dumps(usage_payload, ensure_ascii=False),
                    now,
                ),
            )
            if role == "user":
                cursor = await connection.execute(
                    """
                    SELECT COUNT(*) FROM messages
                    WHERE conversation_id = ? AND role = 'user'
                    """,
                    (conversation_id,),
                )
                count_row = await cursor.fetchone()
                if count_row is None:
                    raise RuntimeError("message_count_unavailable")
                count = int(count_row[0])
                if count == 1:
                    title = "".join(content.split())[:6] or "新会话"
                    await connection.execute(
                        """
                        UPDATE conversations
                        SET title = ?, updated_at = ?
                        WHERE conversation_id = ?
                        """,
                        (title, now, conversation_id),
                    )
                else:
                    await connection.execute(
                        """
                        UPDATE conversations SET updated_at = ?
                        WHERE conversation_id = ?
                        """,
                        (now, conversation_id),
                    )
            else:
                await connection.execute(
                    """
                    UPDATE conversations SET updated_at = ?
                    WHERE conversation_id = ?
                    """,
                    (now, conversation_id),
                )
            await connection.commit()

    async def messages(self, user_id: str, conversation_id: str) -> list[MessageRecord] | None:
        if not await self.owns(user_id, conversation_id):
            return None
        async with aiosqlite.connect(self.path) as connection:
            connection.row_factory = aiosqlite.Row
            cursor = await connection.execute(
                """
                SELECT message_id, role, content, queries_json, charts_json,
                       usage_json, created_at
                FROM messages
                WHERE conversation_id = ?
                ORDER BY message_id
                """,
                (conversation_id,),
            )
            rows = await cursor.fetchall()
        messages: list[MessageRecord] = []
        for row in rows:
            item: dict[str, Any] = dict(row)
            item["queries"] = json.loads(item.pop("queries_json"))
            item["charts"] = json.loads(item.pop("charts_json"))
            item["usage"] = json.loads(item.pop("usage_json"))
            messages.append(MessageRecord.model_validate(item))
        return messages

    async def delete(self, user_id: str, conversation_id: str) -> bool:
        async with self._write_lock, aiosqlite.connect(self.path) as connection:
            await connection.execute("PRAGMA foreign_keys = ON")
            cursor = await connection.execute(
                """
                DELETE FROM conversations
                WHERE conversation_id = ? AND user_id = ?
                """,
                (conversation_id, user_id),
            )
            await connection.commit()
            return cursor.rowcount > 0
