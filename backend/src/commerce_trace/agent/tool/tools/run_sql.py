from __future__ import annotations

import hashlib
import json
import time

from ....models import Evidence, ToolFailure, ToolSuccess
from ...sql_safety import SqlSafetyError, SqlSafetyPolicy
from ..base import SqlExecutor, Tool, ToolContext
from .args import RunSqlArgs


class RunSqlTool(Tool[RunSqlArgs]):
    """执行有返回上限且只读的 SQLite 业务查询。"""

    def __init__(self, executor: SqlExecutor, policy: SqlSafetyPolicy | None = None) -> None:
        """注入 SQL 执行器，并使用给定或默认的安全策略。"""

        self._executor = executor
        self._policy = policy or SqlSafetyPolicy()

    @property
    def name(self) -> str:
        """返回工具注册名称。"""

        return "run_sql"

    @property
    def description(self) -> str:
        """返回提供给大模型的工具说明。"""

        return "执行一条有界的 SQLite 只读业务查询"

    def get_args_schema(self) -> type[RunSqlArgs]:
        """返回 SQL 工具的参数校验模型。"""

        return RunSqlArgs

    async def execute(self, context: ToolContext, args: RunSqlArgs) -> ToolSuccess | ToolFailure:
        """校验并执行查询，把有界结果缓存到当前工具上下文。"""

        try:
            validated = self._policy.validate(args.sql)
        except SqlSafetyError as exc:
            return ToolFailure(
                safe_error_code=exc.code,
                safe_error_message=exc.safe_message,
                retryable=False,
            )
        started = time.perf_counter()
        try:
            rows = await self._executor.execute(validated.normalized_sql, validated.row_limit + 1)
        except Exception:
            return ToolFailure(
                safe_error_code="database_query_failed",
                safe_error_message="查询执行失败，请检查字段、筛选条件或聚合方式",
                retryable=True,
            )
        truncated = len(rows) > validated.row_limit
        rows = rows[: validated.row_limit]
        columns = list(rows[0].keys()) if rows else list(args.expected_columns)
        canonical = json.dumps(rows, ensure_ascii=False, sort_keys=True, default=str)
        result_hash = hashlib.sha256(canonical.encode()).hexdigest()
        data = {
            "sql": validated.normalized_sql,
            "purpose": args.purpose,
            "expected_columns": args.expected_columns,
            "columns": columns,
            "row_count": len(rows),
            "preview": rows,
            "execution_time_ms": round((time.perf_counter() - started) * 1000, 3),
            "result_hash": result_hash,
            "truncated": truncated,
        }
        result_id = f"result_{result_hash[:12]}"
        context.query_results[result_id] = data
        data["result_id"] = result_id
        return ToolSuccess(data=data)

    async def on_success(self, context: ToolContext, args: RunSqlArgs, result: ToolSuccess) -> None:
        """把成功查询转换为可追溯证据并按需持久化。"""

        data = result.data
        preview = data.get("preview", [])
        if preview:
            first = preview[0]
            values = "，".join(f"{k}={v}" for k, v in first.items())
            claim = f"{data['purpose']}：{values}"
        else:
            claim = f"{data['purpose']}：当前条件下无结果"

        evidence = Evidence(
            analysis_step=args.purpose,
            tool_call_id=context.tool_call_id,
            claim=claim,
            sql=data["sql"],
            columns=data["columns"],
            row_count=data["row_count"],
            result_hash=data["result_hash"],
            execution_time_ms=data["execution_time_ms"],
            preview=preview,
        )
        result.data["evidence_id"] = evidence.evidence_id
        result_id = data.get("result_id")
        if result_id and result_id in context.query_results:
            context.query_results[result_id]["evidence_id"] = evidence.evidence_id

        if context.store is not None:
            await context.store.save_evidence(
                context.user_id, context.conversation_id, context.request_id, evidence
            )
        context.created_evidence.append(evidence)
