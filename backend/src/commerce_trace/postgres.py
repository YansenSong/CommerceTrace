from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Protocol, cast

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from .contracts import Chart, EventType, Evidence, StreamEvent
from .memory import MemoryRecord, MemoryStatus


class PostgresResources:
    def __init__(self, database_url: str, *, min_size: int = 1, max_size: int = 8) -> None:
        self.pool = AsyncConnectionPool(
            database_url,
            min_size=min_size,
            max_size=max_size,
            open=False,
            kwargs={"row_factory": dict_row},
        )

    async def open(self) -> None:
        await self.pool.open()
        await self.pool.wait()

    async def close(self) -> None:
        await self.pool.close()


class IndexHealth(Protocol):
    async def status(self) -> str: ...


class PostgresStore:
    def __init__(
        self,
        resources: PostgresResources,
        index_health: IndexHealth | None = None,
    ) -> None:
        self.resources = resources
        self.index_health = index_health

    async def health(self) -> dict[str, Any]:
        try:
            async with self.resources.pool.connection() as connection:
                raw_row = await (
                    await connection.execute(
                        """
                        SELECT
                          EXISTS (SELECT 1 FROM ecommerce.orders) AS dataset_ready,
                          EXISTS (SELECT 1 FROM agent_app.memory_records
                                  WHERE status = 'trusted') AS knowledge_ready
                        """
                    )
                ).fetchone()
                row = cast(dict[str, Any] | None, raw_row)
            health = {
                "database": "ready",
                "dataset": "ready" if row and row["dataset_ready"] else "missing",
                "knowledge": "ready" if row and row["knowledge_ready"] else "missing",
            }
            health["derived_index"] = (
                await self.index_health.status() if self.index_health is not None else "disabled"
            )
            return health
        except Exception:
            return {
                "database": "unavailable",
                "dataset": "unknown",
                "knowledge": "unknown",
                "derived_index": "unknown",
            }

    async def ensure_user(self, user_id: str) -> None:
        async with self.resources.pool.connection() as connection:
            await connection.execute(
                """
                INSERT INTO agent_app.anonymous_users (user_id)
                VALUES (%s) ON CONFLICT (user_id) DO NOTHING
                """,
                (user_id,),
            )

    async def ensure_conversation(self, conversation_id: str, user_id: str, title: str) -> None:
        async with self.resources.pool.connection() as connection:
            raw_existing = await (
                await connection.execute(
                    "SELECT user_id FROM agent_app.conversations WHERE conversation_id = %s",
                    (conversation_id,),
                )
            ).fetchone()
            existing = cast(dict[str, Any] | None, raw_existing)
            if existing and existing["user_id"] != user_id:
                raise PermissionError("conversation_not_found")
            await connection.execute(
                """
                INSERT INTO agent_app.conversations
                  (conversation_id, user_id, title)
                VALUES (%s, %s, %s)
                ON CONFLICT (conversation_id) DO UPDATE
                  SET updated_at = now()
                  WHERE agent_app.conversations.user_id = EXCLUDED.user_id
                """,
                (conversation_id, user_id, title[:120]),
            )

    async def save_message(self, conversation_id: str, role: str, content: str) -> None:
        async with self.resources.pool.connection() as connection:
            await connection.execute(
                """
                INSERT INTO agent_app.messages (conversation_id, role, content)
                VALUES (%s, %s, %s)
                """,
                (conversation_id, role, content),
            )
            await connection.execute(
                """
                UPDATE agent_app.conversations SET updated_at = now()
                WHERE conversation_id = %s
                """,
                (conversation_id,),
            )

    async def save_event(self, user_id: str, event: StreamEvent) -> None:
        async with self.resources.pool.connection() as connection:
            owner = await (
                await connection.execute(
                    """
                    SELECT 1 FROM agent_app.conversations
                    WHERE conversation_id = %s AND user_id = %s
                    """,
                    (event.conversation_id, user_id),
                )
            ).fetchone()
            if owner is None:
                raise PermissionError("conversation_not_found")
            await connection.execute(
                """
                INSERT INTO agent_app.stream_events
                  (event_id, conversation_id, request_id, event_type, payload, created_at)
                VALUES (%s, %s, %s, %s, %s::jsonb, %s)
                ON CONFLICT (event_id) DO NOTHING
                """,
                (
                    event.event_id,
                    event.conversation_id,
                    event.request_id,
                    event.event.value,
                    json.dumps(event.payload, ensure_ascii=False, default=str),
                    event.timestamp,
                ),
            )

    async def save_tool_started(
        self,
        user_id: str,
        conversation_id: str,
        request_id: str,
        tool_call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> None:
        async with self.resources.pool.connection() as connection:
            await connection.execute(
                """
                INSERT INTO agent_app.tool_calls
                  (tool_call_id, conversation_id, request_id, tool_name, arguments, status)
                SELECT %s, c.conversation_id, %s, %s, %s::jsonb, 'started'
                FROM agent_app.conversations c
                WHERE c.conversation_id = %s AND c.user_id = %s
                ON CONFLICT (tool_call_id) DO NOTHING
                """,
                (
                    tool_call_id,
                    request_id,
                    tool_name,
                    json.dumps(arguments, ensure_ascii=False, default=str),
                    conversation_id,
                    user_id,
                ),
            )

    async def save_tool_result(
        self,
        user_id: str,
        tool_call_id: str,
        *,
        success: bool,
        summary: dict[str, Any],
    ) -> None:
        async with self.resources.pool.connection() as connection:
            owner = await (
                await connection.execute(
                    """
                    SELECT 1
                    FROM agent_app.tool_calls tc
                    JOIN agent_app.conversations c USING (conversation_id)
                    WHERE tc.tool_call_id = %s AND c.user_id = %s
                    """,
                    (tool_call_id, user_id),
                )
            ).fetchone()
            if owner is None:
                raise PermissionError("tool_call_not_found")
            await connection.execute(
                """
                UPDATE agent_app.tool_calls
                SET status = %s
                WHERE tool_call_id = %s
                """,
                ("completed" if success else "failed", tool_call_id),
            )
            await connection.execute(
                """
                INSERT INTO agent_app.tool_results
                  (tool_call_id, success, result_summary)
                VALUES (%s, %s, %s::jsonb)
                """,
                (
                    tool_call_id,
                    success,
                    json.dumps(summary, ensure_ascii=False, default=str),
                ),
            )

    async def save_evidence(
        self,
        user_id: str,
        conversation_id: str,
        request_id: str,
        evidence: Evidence,
    ) -> None:
        async with self.resources.pool.connection() as connection:
            await connection.execute(
                """
                INSERT INTO agent_app.evidence
                  (evidence_id, conversation_id, request_id, analysis_step,
                   tool_call_id, claim, sql, columns_json, row_count,
                   result_hash, execution_time_ms, preview_json, executed_at)
                SELECT %s, c.conversation_id, %s, %s, %s, %s, %s,
                       %s::jsonb, %s, %s, %s, %s::jsonb, %s
                FROM agent_app.conversations c
                WHERE c.conversation_id = %s AND c.user_id = %s
                """,
                (
                    evidence.evidence_id,
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
                    evidence.executed_at,
                    conversation_id,
                    user_id,
                ),
            )

    async def save_chart(
        self,
        user_id: str,
        conversation_id: str,
        request_id: str,
        chart: Chart,
    ) -> None:
        async with self.resources.pool.connection() as connection:
            await connection.execute(
                """
                INSERT INTO agent_app.charts
                  (chart_id, conversation_id, request_id, evidence_id,
                   chart_type, title, figure_json)
                SELECT %s, c.conversation_id, %s, %s, %s, %s, %s::jsonb
                FROM agent_app.conversations c
                WHERE c.conversation_id = %s AND c.user_id = %s
                """,
                (
                    chart.chart_id,
                    request_id,
                    chart.evidence_id,
                    chart.chart_type,
                    chart.title,
                    json.dumps(chart.figure, ensure_ascii=False, default=str),
                    conversation_id,
                    user_id,
                ),
            )

    async def list_conversations(
        self, user_id: str, limit: int, offset: int
    ) -> list[dict[str, Any]]:
        async with self.resources.pool.connection() as connection:
            rows = await (
                await connection.execute(
                    """
                    SELECT conversation_id, title, created_at, updated_at
                    FROM agent_app.conversations
                    WHERE user_id = %s
                    ORDER BY updated_at DESC, conversation_id
                    LIMIT %s OFFSET %s
                    """,
                    (user_id, limit, offset),
                )
            ).fetchall()
        return [dict(row) for row in rows]

    async def replay_conversation(
        self, user_id: str, conversation_id: str
    ) -> dict[str, Any] | None:
        async with self.resources.pool.connection() as connection:
            conversation = await (
                await connection.execute(
                    """
                    SELECT conversation_id, title, created_at, updated_at
                    FROM agent_app.conversations
                    WHERE conversation_id = %s AND user_id = %s
                    """,
                    (conversation_id, user_id),
                )
            ).fetchone()
            if conversation is None:
                return None
            messages = await (
                await connection.execute(
                    """
                    SELECT role, content, created_at
                    FROM agent_app.messages
                    WHERE conversation_id = %s
                    ORDER BY created_at, message_id
                    """,
                    (conversation_id,),
                )
            ).fetchall()
            raw_events = await (
                await connection.execute(
                    """
                    SELECT event_id, request_id, event_type, payload, created_at
                    FROM agent_app.stream_events
                    WHERE conversation_id = %s
                    ORDER BY created_at, event_id
                    """,
                    (conversation_id,),
                )
            ).fetchall()
            events = cast(list[dict[str, Any]], raw_events)
            evidence = await (
                await connection.execute(
                    "SELECT * FROM agent_app.evidence WHERE conversation_id = %s",
                    (conversation_id,),
                )
            ).fetchall()
            charts = await (
                await connection.execute(
                    "SELECT * FROM agent_app.charts WHERE conversation_id = %s",
                    (conversation_id,),
                )
            ).fetchall()
            tool_calls = await (
                await connection.execute(
                    """
                    SELECT tool_call_id, request_id, tool_name, arguments, status, created_at
                    FROM agent_app.tool_calls
                    WHERE conversation_id = %s
                    ORDER BY created_at, tool_call_id
                    """,
                    (conversation_id,),
                )
            ).fetchall()
            tool_results = await (
                await connection.execute(
                    """
                    SELECT tr.tool_result_id, tr.tool_call_id, tr.success,
                           tr.result_summary, tr.created_at
                    FROM agent_app.tool_results tr
                    JOIN agent_app.tool_calls tc USING (tool_call_id)
                    WHERE tc.conversation_id = %s
                    ORDER BY tr.created_at, tr.tool_result_id
                    """,
                    (conversation_id,),
                )
            ).fetchall()
        serialized_events = [
            StreamEvent(
                event_id=row["event_id"],
                event=EventType(row["event_type"]),
                conversation_id=conversation_id,
                request_id=row["request_id"],
                timestamp=row["created_at"],
                payload=row["payload"],
            ).model_dump(mode="json")
            for row in events
        ]
        return {
            "conversation": dict(conversation),
            "messages": [dict(row) for row in messages],
            "events": serialized_events,
            "tool_calls": [dict(row) for row in tool_calls],
            "tool_results": [dict(row) for row in tool_results],
            "evidence": [dict(row) for row in evidence],
            "charts": [dict(row) for row in charts],
        }

    async def upsert_memory(self, record: MemoryRecord) -> MemoryRecord:
        async with self.resources.pool.connection() as connection:
            row = await (
                await connection.execute(
                    """
                    INSERT INTO agent_app.memory_records
                      (memory_id, dedupe_key, question, analysis_step, normalized_sql,
                       tables_and_columns, schema_fingerprint, metric_versions,
                       execution_time_ms, row_count, column_names, limited_summary,
                       result_hash, status, source, created_at, last_verified_at)
                    VALUES
                      (%s, %s, %s, %s, %s, %s::jsonb, %s, %s::jsonb,
                       %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (dedupe_key) DO UPDATE SET
                      status = CASE
                        WHEN EXCLUDED.status = 'candidate'
                        THEN agent_app.memory_records.status
                        ELSE EXCLUDED.status
                      END,
                      last_verified_at = COALESCE(
                        EXCLUDED.last_verified_at, agent_app.memory_records.last_verified_at
                      )
                    RETURNING *
                    """,
                    (
                        record.memory_id,
                        record.dedupe_key,
                        record.question,
                        record.analysis_step,
                        record.normalized_sql,
                        json.dumps(record.tables_and_columns, ensure_ascii=False),
                        record.schema_fingerprint,
                        json.dumps(record.metric_versions, ensure_ascii=False),
                        record.execution_time_ms,
                        record.row_count,
                        json.dumps(record.column_names, ensure_ascii=False),
                        record.limited_summary,
                        record.result_hash,
                        record.status.value,
                        record.source,
                        record.created_at,
                        record.last_verified_at,
                    ),
                )
            ).fetchone()
        if row is None:
            raise RuntimeError("memory upsert returned no row")
        return self._memory_from_row(dict(row))

    async def list_memories(self, statuses: set[MemoryStatus] | None = None) -> list[MemoryRecord]:
        parameters: tuple[Any, ...] = ()
        where = ""
        if statuses:
            where = "WHERE status = ANY(%s)"
            parameters = ([status.value for status in statuses],)
        async with self.resources.pool.connection() as connection:
            rows = await (
                await connection.execute(
                    f"SELECT * FROM agent_app.memory_records {where} ORDER BY created_at",
                    parameters,
                )
            ).fetchall()
        return [self._memory_from_row(dict(row)) for row in rows]

    async def clear_candidates(self) -> int:
        async with self.resources.pool.connection() as connection:
            cursor = await connection.execute(
                "DELETE FROM agent_app.memory_records WHERE status = 'candidate'"
            )
            return max(0, cursor.rowcount or 0)

    @staticmethod
    def _memory_from_row(row: dict[str, Any]) -> MemoryRecord:
        return MemoryRecord(
            memory_id=row["memory_id"],
            question=row["question"],
            analysis_step=row["analysis_step"],
            normalized_sql=row["normalized_sql"],
            tables_and_columns=list(row["tables_and_columns"]),
            schema_fingerprint=row["schema_fingerprint"],
            metric_versions=dict(row["metric_versions"]),
            execution_time_ms=float(row["execution_time_ms"]),
            row_count=int(row["row_count"]),
            column_names=list(row["column_names"]),
            limited_summary=row["limited_summary"],
            result_hash=row["result_hash"],
            status=MemoryStatus(row["status"]),
            source=row["source"],
            created_at=cast(datetime, row["created_at"]),
            last_verified_at=cast(datetime | None, row["last_verified_at"]),
        )


class PostgresSqlExecutor:
    def __init__(
        self,
        resources: PostgresResources,
        *,
        statement_timeout_ms: int = 5_000,
    ) -> None:
        self.resources = resources
        self.statement_timeout_ms = statement_timeout_ms

    async def execute(self, sql: str, row_limit: int) -> list[dict[str, Any]]:
        async with self.resources.pool.connection() as connection, connection.transaction():
            await connection.execute("SET TRANSACTION READ ONLY")
            await connection.execute(
                "SELECT set_config('statement_timeout', %s, true)",
                (str(self.statement_timeout_ms),),
            )
            if sql.upper().startswith("EXPLAIN "):
                bounded_sql = sql
            else:
                bounded_sql = f"SELECT * FROM ({sql}) AS commerce_trace_result LIMIT %s"
            cursor = await connection.execute(
                bounded_sql,
                () if sql.upper().startswith("EXPLAIN ") else (row_limit,),
            )
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]
