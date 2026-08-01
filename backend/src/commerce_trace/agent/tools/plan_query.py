from __future__ import annotations

import re
import sqlite3
import time
from typing import Annotated, Any

import sqlglot
from langchain.tools import ToolRuntime, tool
from sqlglot import exp
from sqlglot.errors import ParseError

from ..sql_safety import SqlSafetyError
from .context import AgentContext
from .run_sql import _execute_sql

_FULL_SCAN = re.compile(r"^SCAN\s+(?:[A-Za-z_][\w]*\.)?([A-Za-z_][\w]*)")


@tool
async def plan_query(
    sql: Annotated[str, "仅访问 ecommerce 白名单表的单条只读 SQLite 查询"],
    purpose: Annotated[str, "本次查询要验证的分析目的"],
    runtime: ToolRuntime[AgentContext],
) -> dict[str, Any]:
    """校验并生成 EXPLAIN QUERY PLAN，不执行查询、不返回任何业务数据。

    执行 SQL 前必须先调用本工具查看执行计划；计划中的全表扫描或过大结果应在执行前收敛。
    """

    context = runtime.context
    try:
        validated = context.sql_policy.validate(sql)
    except SqlSafetyError as exc:
        return {
            "success": False,
            "phase": "validate",
            "error": exc.code,
            "message": exc.safe_message,
        }

    plan_sql = f"EXPLAIN QUERY PLAN {validated.normalized_sql}"
    started = time.perf_counter()
    try:
        rows, _ = await _execute_sql(
            context.database_path,
            plan_sql,
            validated.row_limit,
            context.statement_timeout_ms,
        )
    except sqlite3.Error:
        return {
            "success": False,
            "phase": "plan",
            "error": "plan_failed",
            "message": "执行计划生成失败，请检查 SQL",
        }

    details = [str(row.get("detail", "")) for row in rows]
    alias_map = _alias_map(validated.normalized_sql)
    scans = _full_scan_tables(details)
    return {
        "success": True,
        "phase": "plan",
        "normalized_sql": validated.normalized_sql,
        "plan": details,
        "full_scan_tables": [alias_map.get(scan, scan) for scan in scans],
        "execution_time_ms": (time.perf_counter() - started) * 1_000,
    }


def _full_scan_tables(plan_details: list[str]) -> list[str]:
    """提取 EXPLAIN QUERY PLAN 中的全表扫描目标（可能是别名）。"""

    tables: list[str] = []
    for line in plan_details:
        match = _FULL_SCAN.match(line)
        if match:
            tables.append(match.group(1))
    return tables


def _alias_map(sql: str) -> dict[str, str]:
    """从规范化 SQL 提取表别名 -> 真实表名的映射，用于解析全表扫描目标。"""

    try:
        statement = sqlglot.parse_one(sql, read="sqlite")
    except ParseError:
        return {}
    mapping: dict[str, str] = {}
    for table in statement.find_all(exp.Table):
        if table.alias:
            mapping[table.alias.lower()] = table.name.lower()
    return mapping
