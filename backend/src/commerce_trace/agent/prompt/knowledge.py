"""Business rules and metrics injected into the system prompt context."""

from __future__ import annotations

from typing import Any

RULES: list[dict[str, str]] = [
    {
        "id": "attribution",
        "text": (
            "- 多步归因只描述数据中的主要相关因素、变化贡献和伴随现象。\n"
            "- 不把观察性 SQL 结果表述为严格因果。\n"
            "- 如果 Evidence 不足，明确列出未完成步骤，不补写推测。\n"
            "- 最终回答先给结论，再列 Evidence、图表与口径。"
        ),
    },
    {
        "id": "value-exploration",
        "text": (
            "只允许探索地区、获客渠道、订单渠道、订单状态、品类与支付方式等低基数非敏感字段。\n"
            "客户姓名、地址、联系方式和自由文本不能用于值级探索。"
        ),
    },
]

METRICS: list[dict[str, Any]] = [
    {
        "id": "revenue",
        "name": "销售额",
        "version": "1",
        "definition": "已支付或已完成订单的成交总额",
        "expression": "SUM(ecommerce.orders.total_amount)",
        "filters": ["orders.status IN ('paid', 'completed')"],
    },
    {
        "id": "net_revenue",
        "name": "净销售额",
        "version": "1",
        "definition": "销售额减去退款金额",
        "expression": "revenue - SUM(ecommerce.refunds.amount)",
    },
    {
        "id": "order_count",
        "name": "订单量",
        "version": "1",
        "definition": "订单记录数；回答时必须说明是否排除取消订单",
        "expression": "COUNT(DISTINCT ecommerce.orders.order_id)",
    },
    {
        "id": "aov",
        "name": "客单价",
        "version": "1",
        "definition": "销售额除以非取消订单量",
        "expression": "revenue / NULLIF(order_count, 0)",
    },
    {
        "id": "refund_rate",
        "name": "退款率",
        "version": "1",
        "definition": "退款订单数除以已支付订单数",
        "expression": "refund_order_count / NULLIF(paid_order_count, 0)",
    },
]
