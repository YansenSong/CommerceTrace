"""注入系统提示词的电商 Schema 目录。"""

from __future__ import annotations

import hashlib
import json
from typing import Any

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


def schema_fingerprint(catalog: dict[str, Any] | None = None) -> str:
    """对规范化后的 Schema 目录计算稳定的 SHA-256 指纹。"""

    if catalog is None:
        catalog = SCHEMA_CATALOG
    canonical = json.dumps(catalog, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()
