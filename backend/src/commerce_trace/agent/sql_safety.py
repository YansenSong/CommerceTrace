from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from ..semantic import COMMERCE_SEMANTIC_MODEL, BusinessSemanticModel


class SqlSafetyError(ValueError):
    """表示带有安全错误码、可向用户展示的 SQL 校验异常。"""

    def __init__(self, code: str, message: str) -> None:
        """保存内部错误码和经过脱敏的错误消息。"""

        super().__init__(message)
        self.code = code
        self.safe_message = message


@dataclass(frozen=True)
class ValidatedSql:
    """保存安全校验后的 SQL、行数上限和探索类型。"""

    normalized_sql: str
    row_limit: int
    is_distinct_exploration: bool


class SqlSafetyPolicy:
    """限制查询范围、语句类型、字段探索和危险数据库函数。"""
    dangerous_functions = {
        "pg_sleep",
        "dblink",
        "lo_import",
        "lo_export",
        "pg_read_file",
        "pg_read_binary_file",
        "pg_ls_dir",
        "current_setting",
    }

    def __init__(
        self,
        max_rows: int = 500,
        max_distinct_values: int = 50,
        semantic_model: BusinessSemanticModel = COMMERCE_SEMANTIC_MODEL,
    ) -> None:
        """设置普通查询和去重探索查询的最大返回行数。"""

        self.max_rows = max_rows
        self.max_distinct_values = max_distinct_values
        self.allowed_schema = semantic_model.schema_name
        self.allowed_tables = semantic_model.allowed_tables
        self.exploration_columns = semantic_model.exploration_columns
        self.sensitive_columns = semantic_model.sensitive_columns

    def validate(self, sql: str) -> ValidatedSql:
        """解析并校验 SQL，返回可安全执行的规范化查询。"""

        raw = sql.strip().rstrip(";").strip()
        if not raw:
            raise SqlSafetyError("empty_sql", "查询不能为空")
        try:
            statements = sqlglot.parse(raw, read="sqlite")
        except ParseError as exc:
            raise SqlSafetyError("invalid_sql", "SQL 无法解析") from exc
        if len(statements) != 1:
            raise SqlSafetyError("multiple_statements", "只允许执行一条查询")

        explain = raw.upper().startswith("EXPLAIN ")
        statement: exp.Expr
        if explain:
            explained = raw[8:].strip()
            if explained.upper().startswith(("ANALYZE", "(")):
                raise SqlSafetyError("unsafe_explain", "只允许不执行查询的 EXPLAIN")
            try:
                parsed_explain = sqlglot.parse_one(explained, read="sqlite")
            except ParseError as exc:
                raise SqlSafetyError("invalid_sql", "EXPLAIN 中的 SQL 无法解析") from exc
            if parsed_explain is None:
                raise SqlSafetyError("invalid_sql", "EXPLAIN 中的 SQL 无法解析")
            statement = parsed_explain
        else:
            parsed = statements[0]
            if parsed is None:
                raise SqlSafetyError("invalid_sql", "SQL 无法解析")
            statement = parsed

        if not isinstance(statement, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
            raise SqlSafetyError("not_read_only", "只允许只读 SELECT 查询")

        forbidden_nodes = (
            exp.Insert,
            exp.Update,
            exp.Delete,
            exp.Create,
            exp.Drop,
            exp.Alter,
            exp.Command,
            exp.Copy,
            exp.Merge,
            exp.Transaction,
        )
        if any(statement.find(node_type) for node_type in forbidden_nodes):
            raise SqlSafetyError("not_read_only", "查询包含禁止的写入或管理操作")

        cte_names = {
            cte.alias_or_name.lower() for cte in statement.find_all(exp.CTE) if cte.alias_or_name
        }
        for table in statement.find_all(exp.Table):
            table_name = table.name.lower()
            schema = (table.db or "").lower()
            if table_name in cte_names and not schema:
                continue
            if schema != self.allowed_schema or table_name not in self.allowed_tables:
                raise SqlSafetyError("schema_denied", "查询只能访问 ecommerce 业务数据")

        for function in statement.find_all(exp.Func):
            function_name = (function.name or function.sql_name()).lower()
            if function_name in self.dangerous_functions:
                raise SqlSafetyError("dangerous_function", "查询使用了禁止的数据库函数")

        is_distinct = bool(isinstance(statement, exp.Select) and statement.args.get("distinct"))
        if is_distinct and isinstance(statement, exp.Select):
            self._validate_distinct_exploration(statement)

        limit = self.max_distinct_values if is_distinct else self.max_rows
        normalized = statement.sql(dialect="sqlite", pretty=False)
        if explain:
            normalized = f"EXPLAIN QUERY PLAN {normalized}"
        return ValidatedSql(
            normalized_sql=normalized,
            row_limit=limit,
            is_distinct_exploration=is_distinct,
        )

    def _validate_distinct_exploration(self, statement: exp.Select) -> None:
        """确保 DISTINCT 探索只读取单个白名单非敏感字段。"""

        selected = list(statement.expressions)
        tables = [table for table in statement.find_all(exp.Table)]
        if len(selected) != 1 or len(tables) != 1:
            raise SqlSafetyError(
                "distinct_not_allowed",
                "值级探索只允许查询一个白名单字段",
            )
        column = selected[0]
        if not isinstance(column, exp.Column):
            raise SqlSafetyError("distinct_not_allowed", "值级探索字段不受支持")
        table_name = tables[0].name.lower()
        column_name = column.name.lower()
        if (
            (table_name, column_name) in self.sensitive_columns
            or (
                table_name,
                column_name,
            )
            not in self.exploration_columns
        ):
            raise SqlSafetyError("sensitive_exploration", "该字段不允许值级探索")
