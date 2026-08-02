"""Controlled prepare-then-execute interface for read-only business queries."""

from __future__ import annotations

import asyncio
import re
import sqlite3
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from .agent.sql_safety import SqlSafetyError, SqlSafetyPolicy
from .models import PreparedQuery, QueryResult, QueryTrace
from .semantic import BusinessSemanticModel

_FULL_SCAN = re.compile(r"^SCAN\s+(?:[A-Za-z_][\w]*\.)?([A-Za-z_][\w]*)")


class QueryEngineError(ValueError):
    """A safe, structured failure from query preparation or execution."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(code)
        self.code = code
        self.safe_message = message


class QueryEngine:
    """Prepare immutable queries and execute only issued capabilities."""

    def __init__(
        self,
        *,
        database_path: Path,
        statement_timeout_ms: int,
        sql_policy: SqlSafetyPolicy,
        semantic_model: BusinessSemanticModel,
    ) -> None:
        self._database_path = database_path
        self._statement_timeout_ms = statement_timeout_ms
        self._sql_policy = sql_policy
        self._semantic_model = semantic_model
        self._prepared: dict[str, PreparedQuery] = {}
        self._results: dict[str, QueryResult] = {}
        self._lock = asyncio.Lock()

    async def prepare(self, sql: str, *, purpose: str) -> PreparedQuery:
        try:
            validated = self._sql_policy.validate(sql)
        except SqlSafetyError as exc:
            raise QueryEngineError(exc.code, exc.safe_message) from exc

        plan_sql = f"EXPLAIN QUERY PLAN {validated.normalized_sql}"
        try:
            rows, _ = await _execute_sql(
                self._database_path,
                plan_sql,
                validated.row_limit,
                self._statement_timeout_ms,
            )
        except sqlite3.Error as exc:
            raise QueryEngineError(
                "plan_failed",
                "执行计划生成失败，请检查 SQL",
            ) from exc

        plan = [str(row.get("detail", "")) for row in rows]
        alias_map = _alias_map(validated.normalized_sql)
        full_scans = [
            alias_map.get(table, table) for table in _full_scan_tables(plan)
        ]
        prepared = PreparedQuery(
            prepared_query_id=f"prepared_{uuid4().hex[:16]}",
            purpose=purpose,
            normalized_sql=validated.normalized_sql,
            plan=plan,
            full_scan_tables=full_scans,
            semantic_fingerprint=self._semantic_model.fingerprint(),
        )
        async with self._lock:
            self._prepared[prepared.prepared_query_id] = prepared
        return prepared

    async def execute(self, prepared_query_id: str) -> QueryResult:
        async with self._lock:
            cached = self._results.get(prepared_query_id)
            prepared = self._prepared.get(prepared_query_id)
        if cached is not None:
            return cached
        if prepared is None:
            raise QueryEngineError(
                "prepared_query_not_found",
                "查询必须先通过准备和校验",
            )
        if prepared.semantic_fingerprint != self._semantic_model.fingerprint():
            raise QueryEngineError(
                "semantic_model_changed",
                "业务语义模型已变更，请重新准备查询",
            )

        started = time.perf_counter()
        validated = self._sql_policy.validate(prepared.normalized_sql)
        try:
            rows, truncated = await _execute_sql(
                self._database_path,
                prepared.normalized_sql,
                validated.row_limit,
                self._statement_timeout_ms,
            )
        except sqlite3.Error as exc:
            raise QueryEngineError(
                "query_failed",
                "查询执行失败，请检查字段、筛选条件或聚合方式",
            ) from exc

        result = QueryResult(
            trace=QueryTrace(
                query_id=f"query_{uuid4().hex[:12]}",
                purpose=prepared.purpose,
                sql=prepared.normalized_sql,
                columns=list(rows[0]) if rows else [],
                row_count=len(rows),
                preview=rows[:20],
                execution_time_ms=(time.perf_counter() - started) * 1_000,
                truncated=truncated,
            ),
            rows=rows,
        )
        async with self._lock:
            existing = self._results.setdefault(prepared_query_id, result)
        return existing


async def _execute_sql(
    database_path: Path,
    sql: str,
    row_limit: int,
    statement_timeout_ms: int,
) -> tuple[list[dict[str, Any]], bool]:
    def operation() -> tuple[list[dict[str, Any]], bool]:
        connection = sqlite3.connect(":memory:", check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("ATTACH DATABASE ? AS ecommerce", (str(database_path),))
        connection.execute("PRAGMA query_only = ON")
        deadline = time.monotonic() + statement_timeout_ms / 1_000
        connection.set_progress_handler(
            lambda: 1 if time.monotonic() > deadline else 0,
            1_000,
        )
        try:
            if sql.upper().startswith("EXPLAIN "):
                cursor = connection.execute(sql)
                return [dict(row) for row in cursor.fetchall()], False
            cursor = connection.execute(
                f"SELECT * FROM ({sql}) AS commerce_trace_result LIMIT ?",
                (row_limit + 1,),
            )
            fetched = [dict(row) for row in cursor.fetchall()]
            truncated = len(fetched) > row_limit
            return fetched[:row_limit], truncated
        finally:
            connection.close()

    return await asyncio.to_thread(operation)


def _full_scan_tables(plan_details: list[str]) -> list[str]:
    tables: list[str] = []
    for line in plan_details:
        match = _FULL_SCAN.match(line)
        if match:
            tables.append(match.group(1))
    return tables


def _alias_map(sql: str) -> dict[str, str]:
    try:
        statement = sqlglot.parse_one(sql, read="sqlite")
    except ParseError:
        return {}
    mapping: dict[str, str] = {}
    for table in statement.find_all(exp.Table):
        if table.alias:
            mapping[table.alias.lower()] = table.name.lower()
    return mapping
