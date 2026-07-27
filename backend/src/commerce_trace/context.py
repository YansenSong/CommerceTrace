from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Protocol

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, Field

SCHEMA_CATALOG: dict[str, Any] = {
    "version": "1.0.0",
    "schema": "ecommerce",
    "tables": {
        "customers": {
            "description": "客户、地区、注册时间与获客渠道",
            "columns": {
                "customer_id": "integer 主键",
                "name": "text 客户姓名（敏感）",
                "region": "text 地区",
                "registered_at": "text ISO-8601 注册时间",
                "acquisition_channel": "text 获客渠道",
            },
        },
        "categories": {
            "description": "商品品类",
            "columns": {"category_id": "integer 主键", "name": "text 品类名"},
        },
        "products": {
            "description": "商品与当前价格成本",
            "columns": {
                "product_id": "integer 主键",
                "category_id": "integer 外键 categories",
                "name": "text 商品名",
                "current_price": "real 当前售价",
                "current_cost": "real 当前成本",
            },
        },
        "orders": {
            "description": "订单时间、状态、客户和渠道",
            "columns": {
                "order_id": "integer 主键",
                "customer_id": "integer 外键 customers",
                "ordered_at": "text ISO-8601 下单时间",
                "status": "text paid|completed|cancelled|refunded",
                "channel": "text 渠道",
                "total_amount": "real 成交总额",
            },
        },
        "order_items": {
            "description": "订单商品明细和成交时价格成本",
            "columns": {
                "order_item_id": "integer 主键",
                "order_id": "integer 外键 orders",
                "product_id": "integer 外键 products",
                "quantity": "integer 数量",
                "unit_price": "real 成交单价",
                "unit_cost": "real 成交时成本",
            },
        },
        "payments": {
            "description": "支付时间、方式与金额",
            "columns": {
                "payment_id": "integer 主键",
                "order_id": "integer 外键 orders",
                "paid_at": "text ISO-8601 支付时间",
                "payment_method": "text 支付方式",
                "amount": "real 支付金额",
            },
        },
        "refunds": {
            "description": "退款时间、原因与金额",
            "columns": {
                "refund_id": "integer 主键",
                "order_id": "integer 外键 orders",
                "refunded_at": "text ISO-8601 退款时间",
                "reason": "text 退款原因",
                "amount": "real 退款金额",
            },
        },
        "inventory_snapshots": {
            "description": "商品每日库存",
            "columns": {
                "snapshot_date": "text ISO 日期、联合主键",
                "product_id": "integer 联合主键、外键 products",
                "stock_quantity": "integer 库存量",
            },
        },
    },
    "relationships": [
        "customers.customer_id -> orders.customer_id",
        "orders.order_id -> order_items.order_id",
        "categories.category_id -> products.category_id",
        "products.product_id -> order_items.product_id",
        "orders.order_id -> payments.order_id",
        "orders.order_id -> refunds.order_id",
        "products.product_id -> inventory_snapshots.product_id",
    ],
}


def schema_fingerprint(catalog: dict[str, Any] = SCHEMA_CATALOG) -> str:
    canonical = json.dumps(catalog, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def load_golden_examples(root: Path | None = None) -> list[dict[str, str]]:
    """Load golden SQL YAML files as few-shot examples for the system prompt."""
    examples: list[dict[str, str]] = []
    if root is None or not root.exists():
        return examples
    golden_dir = root / "golden_sql"
    if not golden_dir.exists():
        return examples
    for path in sorted(golden_dir.glob("*.yaml")):
        item: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
        examples.append({
            "question": str(item["question"]),
            "sql": str(item["sql"]),
        })
    return examples


def format_golden_examples(examples: list[dict[str, str]]) -> str:
    """Render golden examples as a few-shot section for the system prompt."""
    if not examples:
        return ""
    lines = ["## 参考 SQL 示例"]
    for index, example in enumerate(examples, start=1):
        lines.append(f"### 示例 {index}：{example['question']}")
        lines.append("```sql")
        lines.append(example["sql"].strip())
        lines.append("```")
        lines.append("")
    return "\n".join(lines)


class RetrievedContext(BaseModel):
    schema_catalog: dict[str, Any]
    schema_fingerprint: str
    schema_version: str
    knowledge_version: str
    rules: list[dict[str, Any]] = Field(default_factory=list)
    metrics: list[dict[str, Any]] = Field(default_factory=list)
    golden_examples: list[dict[str, str]] = Field(default_factory=list)
    degraded: bool = False

    def prompt_section(self) -> str:
        payload = {
            "schema": self.schema_catalog,
            "schema_fingerprint": self.schema_fingerprint,
            "business_rules": self.rules,
            "metrics": self.metrics,
        }
        section = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        few_shot = format_golden_examples(self.golden_examples)
        if few_shot:
            section += "\n\n" + few_shot
        return section


class KnowledgeLoader:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root

    def load(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
        if self.root is None or not self.root.exists():
            return (
                [
                    {
                        "id": "descriptive-attribution",
                        "text": "经营归因只能描述主要相关因素或贡献，不宣称严格因果。",
                    }
                ],
                [
                    {
                        "id": "revenue",
                        "version": "1",
                        "definition": "已支付或已完成订单的成交金额总和",
                    }
                ],
                "1.0.0",
            )
        rules: list[dict[str, Any]] = []
        metrics: list[dict[str, Any]] = []
        for path in sorted((self.root / "rules").glob("*.md")):
            rules.append({"id": path.stem, "text": path.read_text(encoding="utf-8")})
        for path in sorted((self.root / "metrics").glob("*.yaml")):
            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
            metrics.extend(loaded if isinstance(loaded, list) else [loaded])
        version_path = self.root / "VERSION"
        version = version_path.read_text(encoding="utf-8").strip() if version_path.exists() else "1"
        return rules, metrics, version


class SchemaProvider(Protocol):
    async def load(self) -> dict[str, Any]: ...


class StaticSchemaProvider:
    async def load(self) -> dict[str, Any]:
        return deepcopy(SCHEMA_CATALOG)


class ContextAssembler:
    def __init__(
        self,
        knowledge_loader: KnowledgeLoader | None = None,
        *,
        include_knowledge: bool = True,
        include_golden_examples: bool = True,
        schema_provider: SchemaProvider | None = None,
    ) -> None:
        self.knowledge_loader = knowledge_loader or KnowledgeLoader()
        self.include_knowledge = include_knowledge
        self.include_golden_examples = include_golden_examples
        self.schema_provider = schema_provider or StaticSchemaProvider()

    async def assemble(self) -> RetrievedContext:
        schema_catalog = await self.schema_provider.load()
        if self.include_knowledge:
            rules, metrics, knowledge_version = self.knowledge_loader.load()
        else:
            rules, metrics, knowledge_version = [], [], "disabled"
        golden_examples: list[dict[str, str]] = []
        if self.include_golden_examples and self.knowledge_loader.root is not None:
            golden_examples = load_golden_examples(self.knowledge_loader.root)
        return RetrievedContext(
            schema_catalog=schema_catalog,
            schema_fingerprint=schema_fingerprint(schema_catalog),
            schema_version=str(schema_catalog["version"]),
            knowledge_version=knowledge_version,
            rules=rules,
            metrics=metrics,
            golden_examples=golden_examples,
        )
