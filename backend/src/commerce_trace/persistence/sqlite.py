from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar, cast

from ..agent.prompt import SCHEMA_CATALOG
from ..models import Chart, EventType, Evidence, StreamEvent

ResultT = TypeVar("ResultT")


def database_files(path: Path) -> tuple[Path, Path, Path]:
    """根据主库路径推导主库、业务库和 Agent 库的文件路径。"""

    return (
        path,
        path.with_name(f"{path.stem}-ecommerce{path.suffix}"),
        path.with_name(f"{path.stem}-agent{path.suffix}"),
    )


def connect_sqlite(path: Path, *, read_only: bool = False) -> sqlite3.Connection:
    """连接主 SQLite 数据库并挂载业务库和 Agent 库。"""

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
    """管理共享 SQLite 连接，并串行化同一连接上的异步操作。"""

    def __init__(self, path: Path) -> None:
        """保存数据库路径并初始化连接锁。"""

        self.path = path
        self.connection: sqlite3.Connection | None = None
        self.lock = asyncio.Lock()

    async def open(self) -> None:
        """在尚未连接时打开共享 SQLite 连接。"""

        if self.connection is None:
            self.connection = connect_sqlite(self.path)

    async def close(self) -> None:
        """关闭共享连接并清除连接引用。"""

        if self.connection is not None:
            self.connection.close()
            self.connection = None

    async def run(self, operation: Callable[[sqlite3.Connection], ResultT]) -> ResultT:
        """在连接锁保护下运行一个同步数据库操作。"""

        if self.connection is None:
            raise RuntimeError("SQLite resource is not open")
        async with self.lock:
            return operation(self.connection)


class SQLiteSchemaProvider:
    """从 SQLite 校验并加载提供给 Agent 的业务 Schema。"""

    def __init__(self, resources: SQLiteResources) -> None:
        """注入共享 SQLite 资源。"""

        self.resources = resources

    async def load(self) -> dict[str, Any]:
        """校验实际表字段与目录一致后返回 Schema 深拷贝。"""

        def operation(connection: sqlite3.Connection) -> dict[str, Any]:
            """在共享连接中读取并比对业务表字段。"""

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
    """使用 SQLite 持久化用户会话、事件、工具结果、证据和图表。"""

    def __init__(self, resources: SQLiteResources) -> None:
        """注入共享 SQLite 资源。"""

        self.resources = resources

    async def health(self) -> dict[str, Any]:
        """检查数据库连接及业务数据集是否可用。"""

        try:

            def operation(connection: sqlite3.Connection) -> bool:
                """检查业务订单表中是否已有数据。"""

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
        """幂等创建匿名用户记录。"""

        def operation(connection: sqlite3.Connection) -> None:
            """在 Agent 库中插入缺失的匿名用户。"""

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
        """创建会话，或验证并刷新属于指定用户的已有会话。"""

        def operation(connection: sqlite3.Connection) -> None:
            """在事务中校验归属并写入会话记录。"""

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
        """保存消息并刷新所属会话的更新时间。"""

        def operation(connection: sqlite3.Connection) -> None:
            """在事务中插入消息并更新会话时间。"""

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
        """验证会话归属后幂等保存流式事件。"""

        def operation(connection: sqlite3.Connection) -> None:
            """在事务中校验归属并序列化写入事件。"""

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
        """验证会话归属后记录工具调用开始状态。"""

        def operation(connection: sqlite3.Connection) -> None:
            """在事务中校验归属并插入工具调用。"""

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
        """验证工具归属后更新状态并保存结果摘要。"""

        def operation(connection: sqlite3.Connection) -> None:
            """在事务中更新工具调用并插入结果记录。"""

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
        """验证会话归属后保存可追溯查询证据。"""

        def operation(connection: sqlite3.Connection) -> None:
            """在事务中序列化并插入证据记录。"""

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
        """验证会话归属后保存图表定义。"""

        def operation(connection: sqlite3.Connection) -> None:
            """在事务中序列化并插入图表记录。"""

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
        """按更新时间倒序分页列出指定用户的会话。"""

        def operation(connection: sqlite3.Connection) -> list[dict[str, Any]]:
            """查询并转换指定用户的会话行。"""

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
        """读取并组装指定用户的一次完整会话。"""

        def operation(connection: sqlite3.Connection) -> dict[str, Any] | None:
            """查询会话关联记录并还原序列化字段。"""

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
    """使用独立只读连接执行带超时和行数边界的业务 SQL。"""

    def __init__(self, database_path: Path, *, statement_timeout_ms: int = 5_000) -> None:
        """保存数据库路径和单条语句超时时间。"""

        self.database_path = database_path
        self.statement_timeout_ms = statement_timeout_ms

    async def execute(self, sql: str, row_limit: int) -> list[dict[str, Any]]:
        """执行只读 SQL，并将结果行转换为字典。"""

        def operation() -> list[dict[str, Any]]:
            """在独立只读连接中执行查询并确保关闭连接。"""

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
