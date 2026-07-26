from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, TypeVar, cast

from ..context import SCHEMA_CATALOG
from ..contracts import Chart, EventType, Evidence, StreamEvent

ResultT = TypeVar("ResultT")


def database_files(path: Path) -> tuple[Path, Path, Path]:
    return (
        path,
        path.with_name(f"{path.stem}-ecommerce{path.suffix}"),
        path.with_name(f"{path.stem}-agent{path.suffix}"),
    )


def connect_sqlite(path: Path, *, read_only: bool = False) -> sqlite3.Connection:
    main_path, ecommerce_path, agent_path = database_files(path)
    main_path.parent.mkdir(parents=True, exist_ok=True)
    if read_only:
        connection = sqlite3.connect(
            f"file:{main_path}?mode=ro",
            uri=True,
            check_same_thread=False,
        )
        ecommerce_target = f"file:{ecommerce_path}?mode=ro"
        agent_target = f"file:{agent_path}?mode=ro"
    else:
        connection = sqlite3.connect(main_path, check_same_thread=False)
        ecommerce_target = str(ecommerce_path)
        agent_target = str(agent_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("ATTACH DATABASE ? AS ecommerce", (ecommerce_target,))
    connection.execute("ATTACH DATABASE ? AS agent_app", (agent_target,))
    if read_only:
        connection.execute("PRAGMA query_only = ON")
    return connection


class SQLiteResources:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.connection: sqlite3.Connection | None = None
        self.lock = asyncio.Lock()

    async def open(self) -> None:
        if self.connection is None:
            self.connection = connect_sqlite(self.path)

    async def close(self) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    async def run(self, operation: Callable[[sqlite3.Connection], ResultT]) -> ResultT:
        if self.connection is None:
            raise RuntimeError("SQLite resource is not open")
        async with self.lock:
            return operation(self.connection)


class SQLiteSchemaProvider:
    def __init__(self, resources: SQLiteResources) -> None:
        self.resources = resources

    async def load(self) -> dict[str, Any]:
        def operation(connection: sqlite3.Connection) -> dict[str, Any]:
            observed: dict[str, set[str]] = {}
            for table in SCHEMA_CATALOG["tables"]:
                rows = connection.execute(f"PRAGMA ecommerce.table_info('{table}')").fetchall()
                observed[table] = {str(row["name"]) for row in rows}
            expected = {
                table: set(details["columns"])
                for table, details in SCHEMA_CATALOG["tables"].items()
            }
            if observed != expected:
                raise RuntimeError("schema_catalog_mismatch")
            return cast(
                dict[str, Any],
                json.loads(json.dumps(SCHEMA_CATALOG, ensure_ascii=False)),
            )

        return await self.resources.run(operation)


class SQLiteStore:
    def __init__(self, resources: SQLiteResources) -> None:
        self.resources = resources

    async def health(self) -> dict[str, Any]:
        try:

            def operation(connection: sqlite3.Connection) -> bool:
                dataset = connection.execute(
                    "SELECT EXISTS (SELECT 1 FROM ecommerce.orders)"
                ).fetchone()[0]
                return bool(dataset)

            dataset_ready = await self.resources.run(operation)
            return {
                "database": "ready",
                "dataset": "ready" if dataset_ready else "missing",
            }
        except Exception:
            return {
                "database": "unavailable",
                "dataset": "unknown",
            }

    async def ensure_user(self, user_id: str) -> None:
        def operation(connection: sqlite3.Connection) -> None:
            connection.execute(
                """
                INSERT INTO agent_app.anonymous_users (user_id)
                VALUES (?) ON CONFLICT (user_id) DO NOTHING
                """,
                (user_id,),
            )
            connection.commit()

        await self.resources.run(operation)

    async def ensure_conversation(self, conversation_id: str, user_id: str, title: str) -> None:
        def operation(connection: sqlite3.Connection) -> None:
            existing = connection.execute(
                """
                SELECT user_id FROM agent_app.conversations WHERE conversation_id = ?
                """,
                (conversation_id,),
            ).fetchone()
            if existing is not None and existing["user_id"] != user_id:
                raise PermissionError("conversation_not_found")
            now = datetime.utcnow().isoformat()
            connection.execute(
                """
                INSERT INTO agent_app.conversations
                  (conversation_id, user_id, title, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (conversation_id) DO UPDATE SET updated_at = excluded.updated_at
                WHERE agent_app.conversations.user_id = excluded.user_id
                """,
                (conversation_id, user_id, title[:120], now, now),
            )
            connection.commit()

        await self.resources.run(operation)

    async def save_message(self, conversation_id: str, role: str, content: str) -> None:
        def operation(connection: sqlite3.Connection) -> None:
            now = datetime.utcnow().isoformat()
            connection.execute(
                """
                INSERT INTO agent_app.messages (conversation_id, role, content, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (conversation_id, role, content, now),
            )
            connection.execute(
                """
                UPDATE agent_app.conversations SET updated_at = ? WHERE conversation_id = ?
                """,
                (now, conversation_id),
            )
            connection.commit()

        await self.resources.run(operation)

    async def save_event(self, user_id: str, event: StreamEvent) -> None:
        def operation(connection: sqlite3.Connection) -> None:
            owner = connection.execute(
                """
                SELECT 1 FROM agent_app.conversations
                WHERE conversation_id = ? AND user_id = ?
                """,
                (event.conversation_id, user_id),
            ).fetchone()
            if owner is None:
                raise PermissionError("conversation_not_found")
            connection.execute(
                """
                INSERT INTO agent_app.stream_events
                  (event_id, conversation_id, request_id, event_type, payload, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (event_id) DO NOTHING
                """,
                (
                    event.event_id,
                    event.conversation_id,
                    event.request_id,
                    event.event.value,
                    json.dumps(event.payload, ensure_ascii=False, default=str),
                    event.timestamp.isoformat(),
                ),
            )
            connection.commit()

        await self.resources.run(operation)

    async def save_tool_started(
        self,
        user_id: str,
        conversation_id: str,
        request_id: str,
        tool_call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> None:
        def operation(connection: sqlite3.Connection) -> None:
            owner = connection.execute(
                """
                SELECT 1 FROM agent_app.conversations
                WHERE conversation_id = ? AND user_id = ?
                """,
                (conversation_id, user_id),
            ).fetchone()
            if owner is None:
                raise PermissionError("conversation_not_found")
            connection.execute(
                """
                INSERT INTO agent_app.tool_calls
                  (tool_call_id, conversation_id, request_id, tool_name,
                   arguments, status, created_at)
                VALUES (?, ?, ?, ?, ?, 'started', ?)
                ON CONFLICT (tool_call_id) DO NOTHING
                """,
                (
                    tool_call_id,
                    conversation_id,
                    request_id,
                    tool_name,
                    json.dumps(arguments, ensure_ascii=False, default=str),
                    datetime.utcnow().isoformat(),
                ),
            )
            connection.commit()

        await self.resources.run(operation)

    async def save_tool_result(
        self,
        user_id: str,
        tool_call_id: str,
        *,
        success: bool,
        summary: dict[str, Any],
    ) -> None:
        def operation(connection: sqlite3.Connection) -> None:
            owner = connection.execute(
                """
                SELECT 1
                FROM agent_app.tool_calls tc
                JOIN agent_app.conversations c USING (conversation_id)
                WHERE tc.tool_call_id = ? AND c.user_id = ?
                """,
                (tool_call_id, user_id),
            ).fetchone()
            if owner is None:
                raise PermissionError("tool_call_not_found")
            connection.execute(
                """
                UPDATE agent_app.tool_calls SET status = ? WHERE tool_call_id = ?
                """,
                ("completed" if success else "failed", tool_call_id),
            )
            connection.execute(
                """
                INSERT INTO agent_app.tool_results
                  (tool_call_id, success, result_summary, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    tool_call_id,
                    int(success),
                    json.dumps(summary, ensure_ascii=False, default=str),
                    datetime.utcnow().isoformat(),
                ),
            )
            connection.commit()

        await self.resources.run(operation)

    async def save_evidence(
        self,
        user_id: str,
        conversation_id: str,
        request_id: str,
        evidence: Evidence,
    ) -> None:
        def operation(connection: sqlite3.Connection) -> None:
            owner = connection.execute(
                """
                SELECT 1 FROM agent_app.conversations
                WHERE conversation_id = ? AND user_id = ?
                """,
                (conversation_id, user_id),
            ).fetchone()
            if owner is None:
                raise PermissionError("conversation_not_found")
            connection.execute(
                """
                INSERT INTO agent_app.evidence
                  (evidence_id, conversation_id, request_id, analysis_step,
                   tool_call_id, claim, sql, columns_json, row_count,
                   result_hash, execution_time_ms, preview_json, executed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evidence.evidence_id,
                    conversation_id,
                    request_id,
                    evidence.analysis_step,
                    evidence.tool_call_id,
                    evidence.claim,
                    evidence.sql,
                    json.dumps(evidence.columns, ensure_ascii=False),
                    evidence.row_count,
                    evidence.result_hash,
                    evidence.execution_time_ms,
                    json.dumps(evidence.preview, ensure_ascii=False, default=str),
                    evidence.executed_at.isoformat(),
                ),
            )
            connection.commit()

        await self.resources.run(operation)

    async def save_chart(
        self,
        user_id: str,
        conversation_id: str,
        request_id: str,
        chart: Chart,
    ) -> None:
        def operation(connection: sqlite3.Connection) -> None:
            owner = connection.execute(
                """
                SELECT 1 FROM agent_app.conversations
                WHERE conversation_id = ? AND user_id = ?
                """,
                (conversation_id, user_id),
            ).fetchone()
            if owner is None:
                raise PermissionError("conversation_not_found")
            connection.execute(
                """
                INSERT INTO agent_app.charts
                  (chart_id, conversation_id, request_id, evidence_id,
                   chart_type, title, figure_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chart.chart_id,
                    conversation_id,
                    request_id,
                    chart.evidence_id,
                    chart.chart_type,
                    chart.title,
                    json.dumps(chart.figure, ensure_ascii=False, default=str),
                    datetime.utcnow().isoformat(),
                ),
            )
            connection.commit()

        await self.resources.run(operation)

    async def list_conversations(
        self, user_id: str, limit: int, offset: int
    ) -> list[dict[str, Any]]:
        def operation(connection: sqlite3.Connection) -> list[dict[str, Any]]:
            rows = connection.execute(
                """
                SELECT conversation_id, title, created_at, updated_at
                FROM agent_app.conversations
                WHERE user_id = ?
                ORDER BY updated_at DESC, conversation_id
                LIMIT ? OFFSET ?
                """,
                (user_id, limit, offset),
            ).fetchall()
            return [dict(row) for row in rows]

        return await self.resources.run(operation)

    async def replay_conversation(
        self, user_id: str, conversation_id: str
    ) -> dict[str, Any] | None:
        def operation(connection: sqlite3.Connection) -> dict[str, Any] | None:
            conversation = connection.execute(
                """
                SELECT conversation_id, title, created_at, updated_at
                FROM agent_app.conversations
                WHERE conversation_id = ? AND user_id = ?
                """,
                (conversation_id, user_id),
            ).fetchone()
            if conversation is None:
                return None
            messages = connection.execute(
                """
                SELECT role, content, created_at FROM agent_app.messages
                WHERE conversation_id = ? ORDER BY created_at, message_id
                """,
                (conversation_id,),
            ).fetchall()
            event_rows = connection.execute(
                """
                SELECT event_id, request_id, event_type, payload, created_at
                FROM agent_app.stream_events
                WHERE conversation_id = ? ORDER BY created_at, event_id
                """,
                (conversation_id,),
            ).fetchall()
            tool_calls = connection.execute(
                """
                SELECT tool_call_id, request_id, tool_name, arguments, status, created_at
                FROM agent_app.tool_calls
                WHERE conversation_id = ? ORDER BY created_at, tool_call_id
                """,
                (conversation_id,),
            ).fetchall()
            tool_results = connection.execute(
                """
                SELECT tr.tool_result_id, tr.tool_call_id, tr.success,
                       tr.result_summary, tr.created_at
                FROM agent_app.tool_results tr
                JOIN agent_app.tool_calls tc USING (tool_call_id)
                WHERE tc.conversation_id = ?
                ORDER BY tr.created_at, tr.tool_result_id
                """,
                (conversation_id,),
            ).fetchall()
            evidence = connection.execute(
                """
                SELECT * FROM agent_app.evidence
                WHERE conversation_id = ? ORDER BY executed_at, evidence_id
                """,
                (conversation_id,),
            ).fetchall()
            charts = connection.execute(
                """
                SELECT * FROM agent_app.charts
                WHERE conversation_id = ? ORDER BY created_at, chart_id
                """,
                (conversation_id,),
            ).fetchall()

            events = [
                StreamEvent(
                    event_id=row["event_id"],
                    event=EventType(row["event_type"]),
                    conversation_id=conversation_id,
                    request_id=row["request_id"],
                    timestamp=row["created_at"],
                    payload=json.loads(row["payload"]),
                ).model_dump(mode="json")
                for row in event_rows
            ]
            return {
                "conversation": dict(conversation),
                "messages": [dict(row) for row in messages],
                "events": events,
                "tool_calls": [
                    {**dict(row), "arguments": json.loads(row["arguments"])} for row in tool_calls
                ],
                "tool_results": [
                    {
                        **dict(row),
                        "success": bool(row["success"]),
                        "result_summary": json.loads(row["result_summary"]),
                    }
                    for row in tool_results
                ],
                "evidence": [
                    {
                        **dict(row),
                        "columns_json": json.loads(row["columns_json"]),
                        "preview_json": json.loads(row["preview_json"]),
                    }
                    for row in evidence
                ],
                "charts": [
                    {**dict(row), "figure_json": json.loads(row["figure_json"])} for row in charts
                ],
            }

        return await self.resources.run(operation)



class SQLiteSqlExecutor:
    def __init__(self, database_path: Path, *, statement_timeout_ms: int = 5_000) -> None:
        self.database_path = database_path
        self.statement_timeout_ms = statement_timeout_ms

    async def execute(self, sql: str, row_limit: int) -> list[dict[str, Any]]:
        def operation() -> list[dict[str, Any]]:
            connection = connect_sqlite(self.database_path, read_only=True)
            deadline = time.monotonic() + self.statement_timeout_ms / 1000
            connection.set_progress_handler(
                lambda: 1 if time.monotonic() > deadline else 0,
                1_000,
            )
            try:
                if sql.upper().startswith("EXPLAIN "):
                    cursor = connection.execute(sql)
                elif sql.lstrip().upper().startswith(("SELECT ", "WITH ")):
                    cursor = connection.execute(
                        f"SELECT * FROM ({sql}) AS commerce_trace_result LIMIT ?",
                        (row_limit,),
                    )
                else:
                    # Defense-in-depth path: query_only must still reject writes if
                    # application validation is accidentally bypassed.
                    cursor = connection.execute(sql)
                return [dict(row) for row in cursor.fetchall()]
            finally:
                connection.close()

        return operation()
