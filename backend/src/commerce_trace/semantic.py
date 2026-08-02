"""Versioned business semantics shared by prompts, policy, and query planning."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator


class SemanticTable(BaseModel):
    """A governed business object backed by one physical table."""

    model_config = ConfigDict(frozen=True)

    description: str
    columns: dict[str, str]
    exploration_columns: tuple[str, ...] = ()
    sensitive_columns: tuple[str, ...] = ()


class SemanticMetric(BaseModel):
    """A versioned business metric definition."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    source_table: str
    version: str
    definition: str
    expression: str
    filters: tuple[str, ...] = ()
    synonyms: tuple[str, ...] = ()
    deterministic_sql: bool = True


class SemanticDimension(BaseModel):
    """A governed grouping dimension backed by a physical column."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    source_table: str
    column: str
    synonyms: tuple[str, ...] = ()


class SemanticRule(BaseModel):
    """A governed analysis rule supplied to the Agent."""

    model_config = ConfigDict(frozen=True)

    id: str
    text: str


class BusinessSemanticModel(BaseModel):
    """The single source of truth for CommerceTrace business semantics."""

    model_config = ConfigDict(frozen=True)

    version: str
    schema_name: str
    tables: dict[str, SemanticTable]
    relationships: tuple[str, ...]
    metrics: tuple[SemanticMetric, ...]
    dimensions: tuple[SemanticDimension, ...]
    rules: tuple[SemanticRule, ...]

    @model_validator(mode="after")
    def _validate_references_and_names(self) -> BusinessSemanticModel:
        metric_names: set[str] = set()
        dimension_ids: set[str] = set()
        dimension_names: set[str] = set()
        for metric in self.metrics:
            if metric.source_table not in self.tables:
                raise ValueError(f"metric {metric.id} references an unknown table")
            for name in (metric.id, metric.name, *metric.synonyms):
                normalized = name.casefold()
                if normalized in metric_names:
                    raise ValueError(f"duplicate metric name or synonym: {name}")
                metric_names.add(normalized)
        for dimension in self.dimensions:
            if dimension.id in dimension_ids:
                raise ValueError(f"duplicate dimension id: {dimension.id}")
            dimension_ids.add(dimension.id)
            for name in (dimension.id, dimension.name, *dimension.synonyms):
                normalized = name.casefold()
                if normalized in dimension_names:
                    raise ValueError(f"duplicate dimension name or synonym: {name}")
                dimension_names.add(normalized)
            table = self.tables.get(dimension.source_table)
            if table is None or dimension.column not in table.columns:
                raise ValueError(
                    f"dimension {dimension.id} references an unknown column"
                )
        return self

    @property
    def allowed_tables(self) -> set[str]:
        return set(self.tables)

    @property
    def exploration_columns(self) -> set[tuple[str, str]]:
        return {
            (table_name, column)
            for table_name, table in self.tables.items()
            for column in table.exploration_columns
        }

    @property
    def sensitive_columns(self) -> set[tuple[str, str]]:
        return {
            (table_name, column)
            for table_name, table in self.tables.items()
            for column in table.sensitive_columns
        }

    def metric(self, metric_id: str) -> SemanticMetric:
        for metric in self.metrics:
            if metric.id == metric_id:
                return metric
        raise KeyError(metric_id)

    def resolve_metric_id(self, name: str) -> str:
        normalized = name.casefold()
        for metric in self.metrics:
            if normalized in {
                candidate.casefold()
                for candidate in (metric.id, metric.name, *metric.synonyms)
            }:
                return metric.id
        raise KeyError(name)

    def dimension(self, dimension_id: str) -> SemanticDimension:
        for dimension in self.dimensions:
            if dimension.id == dimension_id:
                return dimension
        raise KeyError(dimension_id)

    def resolve_dimension_id(self, name: str) -> str:
        normalized = name.casefold()
        for dimension in self.dimensions:
            if normalized in {
                candidate.casefold()
                for candidate in (
                    dimension.id,
                    dimension.name,
                    *dimension.synonyms,
                )
            }:
                return dimension.id
        raise KeyError(name)

    def render_metric_query(
        self,
        metric_name: str,
        *,
        dimension_ids: tuple[str, ...] = (),
    ) -> str:
        """Expand a supported governed metric into deterministic SQL."""

        metric = self.metric(self.resolve_metric_id(metric_name))
        if not metric.deterministic_sql:
            raise ValueError(f"metric {metric.id} has no deterministic SQL expansion")
        dimensions = [
            self.dimension(self.resolve_dimension_id(item)) for item in dimension_ids
        ]
        if any(item.source_table != metric.source_table for item in dimensions):
            raise ValueError("cross-table metric dimensions are not supported")

        qualified_dimensions = [
            f"{self.schema_name}.{item.source_table}.{item.column}"
            for item in dimensions
        ]
        selections = [
            *(f"{column} AS {item.id}" for column, item in zip(
                qualified_dimensions, dimensions, strict=True
            )),
            f"{metric.expression} AS {metric.id}",
        ]
        sql = (
            f"SELECT {', '.join(selections)} "
            f"FROM {self.schema_name}.{metric.source_table}"
        )
        if metric.filters:
            qualified_filters = [
                condition.replace(
                    f"{metric.source_table}.",
                    f"{self.schema_name}.{metric.source_table}.",
                )
                for condition in metric.filters
            ]
            sql += f" WHERE {' AND '.join(qualified_filters)}"
        if qualified_dimensions:
            sql += f" GROUP BY {', '.join(qualified_dimensions)}"
        return sql

    def schema_catalog(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "schema": self.schema_name,
            "tables": {
                name: {
                    "description": table.description,
                    "columns": dict(table.columns),
                }
                for name, table in self.tables.items()
            },
            "relationships": list(self.relationships),
        }

    def compact_catalog(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "schema": self.schema_name,
            "fingerprint": self.fingerprint(),
            "tables": {
                name: {
                    "description": table.description,
                    "relations": self._relations_for(name),
                }
                for name, table in self.tables.items()
            },
        }

    def fingerprint(self) -> str:
        canonical = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode()).hexdigest()

    def _relations_for(self, table_name: str) -> list[str]:
        marker = f"{table_name}."
        return [relation for relation in self.relationships if marker in relation]


COMMERCE_SEMANTIC_MODEL = BusinessSemanticModel(
    version="1.0.0",
    schema_name="ecommerce",
    tables={
        "customers": SemanticTable(
            description="客户、地区、注册时间与获客渠道",
            columns={
                "customer_id": "integer 主键",
                "name": "text 客户姓名（敏感）",
                "region": "text 地区",
                "registered_at": "text ISO-8601 注册时间",
                "acquisition_channel": "text 获客渠道",
            },
            exploration_columns=("region", "acquisition_channel"),
            sensitive_columns=("name", "address", "phone", "email", "contact"),
        ),
        "categories": SemanticTable(
            description="商品品类",
            columns={"category_id": "integer 主键", "name": "text 品类名"},
            exploration_columns=("name",),
        ),
        "products": SemanticTable(
            description="商品与当前价格成本",
            columns={
                "product_id": "integer 主键",
                "category_id": "integer 外键 categories",
                "name": "text 商品名",
                "current_price": "real 当前售价",
                "current_cost": "real 当前成本",
            },
        ),
        "orders": SemanticTable(
            description="订单时间、状态、客户和渠道",
            columns={
                "order_id": "integer 主键",
                "customer_id": "integer 外键 customers",
                "ordered_at": "text ISO-8601 下单时间",
                "status": "text paid|completed|cancelled|refunded",
                "channel": "text 渠道",
                "total_amount": "real 成交总额",
            },
            exploration_columns=("status", "channel"),
        ),
        "order_items": SemanticTable(
            description="订单商品明细和成交时价格成本",
            columns={
                "order_item_id": "integer 主键",
                "order_id": "integer 外键 orders",
                "product_id": "integer 外键 products",
                "quantity": "integer 数量",
                "unit_price": "real 成交单价",
                "unit_cost": "real 成交时成本",
            },
        ),
        "payments": SemanticTable(
            description="支付时间、方式与金额",
            columns={
                "payment_id": "integer 主键",
                "order_id": "integer 外键 orders",
                "paid_at": "text ISO-8601 支付时间",
                "payment_method": "text 支付方式",
                "amount": "real 支付金额",
            },
            exploration_columns=("payment_method",),
        ),
        "refunds": SemanticTable(
            description="退款时间、原因与金额",
            columns={
                "refund_id": "integer 主键",
                "order_id": "integer 外键 orders",
                "refunded_at": "text ISO-8601 退款时间",
                "reason": "text 退款原因",
                "amount": "real 退款金额",
            },
        ),
        "inventory_snapshots": SemanticTable(
            description="商品每日库存",
            columns={
                "snapshot_date": "text ISO 日期、联合主键",
                "product_id": "integer 联合主键、外键 products",
                "stock_quantity": "integer 库存量",
            },
        ),
    },
    relationships=(
        "customers.customer_id -> orders.customer_id",
        "orders.order_id -> order_items.order_id",
        "categories.category_id -> products.category_id",
        "products.product_id -> order_items.product_id",
        "orders.order_id -> payments.order_id",
        "orders.order_id -> refunds.order_id",
        "products.product_id -> inventory_snapshots.product_id",
    ),
    rules=(
        SemanticRule(
            id="attribution",
            text=(
                "- 多步归因只描述数据中的主要相关因素、变化贡献和伴随现象。\n"
                "- 不把观察性 SQL 结果表述为严格因果。\n"
                "- 如果分析证据不足，明确列出未完成步骤，不补写推测。\n"
                "- 最终回答先给结论，再列证据、图表与口径。"
            ),
        ),
        SemanticRule(
            id="value-exploration",
            text=(
                "只允许探索地区、获客渠道、订单渠道、订单状态、品类与支付方式等"
                "低基数非敏感字段。\n客户姓名、地址、联系方式和自由文本不能用于值级探索。"
            ),
        ),
    ),
    metrics=(
        SemanticMetric(
            id="revenue",
            name="销售额",
            source_table="orders",
            version="1",
            definition="已支付或已完成订单的成交总额",
            expression="SUM(ecommerce.orders.total_amount)",
            filters=("orders.status IN ('paid', 'completed')",),
            synonyms=("成交额", "营业额"),
        ),
        SemanticMetric(
            id="net_revenue",
            name="净销售额",
            source_table="orders",
            version="1",
            definition="销售额减去退款金额",
            expression="revenue - SUM(ecommerce.refunds.amount)",
            deterministic_sql=False,
        ),
        SemanticMetric(
            id="order_count",
            name="订单量",
            source_table="orders",
            version="1",
            definition="订单记录数；回答时必须说明是否排除取消订单",
            expression="COUNT(DISTINCT ecommerce.orders.order_id)",
        ),
        SemanticMetric(
            id="aov",
            name="客单价",
            source_table="orders",
            version="1",
            definition="销售额除以非取消订单量",
            expression="revenue / NULLIF(order_count, 0)",
            deterministic_sql=False,
        ),
        SemanticMetric(
            id="refund_rate",
            name="退款率",
            source_table="orders",
            version="1",
            definition="退款订单数除以已支付订单数",
            expression="refund_order_count / NULLIF(paid_order_count, 0)",
            deterministic_sql=False,
        ),
    ),
    dimensions=(
        SemanticDimension(
            id="order_channel",
            name="订单渠道",
            source_table="orders",
            column="channel",
            synonyms=("成交渠道",),
        ),
        SemanticDimension(
            id="order_status",
            name="订单状态",
            source_table="orders",
            column="status",
        ),
        SemanticDimension(
            id="customer_region",
            name="客户地区",
            source_table="customers",
            column="region",
        ),
        SemanticDimension(
            id="acquisition_channel",
            name="获客渠道",
            source_table="customers",
            column="acquisition_channel",
        ),
        SemanticDimension(
            id="category",
            name="商品品类",
            source_table="categories",
            column="name",
        ),
        SemanticDimension(
            id="payment_method",
            name="支付方式",
            source_table="payments",
            column="payment_method",
        ),
    ),
)
