from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from typing import Any
from uuid import uuid4

import httpx

from .contracts import LlmMessage, LlmResponse, ToolCall, ToolSchema


def _http_proxy_from_environment() -> str | None:
    for variable in (
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "ALL_PROXY",
        "https_proxy",
        "http_proxy",
        "all_proxy",
    ):
        value = os.environ.get(variable)
        if value and value.startswith(("http://", "https://")):
            return value
    return None


class LlmService(ABC):
    @abstractmethod
    async def complete(
        self,
        messages: list[LlmMessage],
        tools: list[ToolSchema],
        system_prompt: str,
    ) -> LlmResponse:
        raise NotImplementedError


class ScriptedLlm(LlmService):
    """Deterministic tool-calling test double; never used by the application runtime."""

    async def complete(
        self,
        messages: list[LlmMessage],
        tools: list[ToolSchema],
        system_prompt: str,
    ) -> LlmResponse:
        question = next(
            (message.content for message in messages if message.role == "user"),
            "",
        )
        attribution = any(
            marker in question
            for marker in ("为什么", "原因", "驱动", "贡献", "主要来自", "相关因素")
        )
        tool_messages = [message for message in messages if message.role == "tool"]
        sql_messages = [
            message for message in tool_messages if '"tool_name": "run_sql"' in message.content
        ]
        chart_messages = [
            message
            for message in tool_messages
            if '"tool_name": "visualize_data"' in message.content
        ]
        if sql_messages:
            last_result = json.loads(sql_messages[-1].content)
            if not last_result.get("success") and last_result.get("retryable"):
                previous_call = next(
                    (
                        call
                        for message in reversed(messages)
                        if message.role == "assistant"
                        for call in message.tool_calls
                        if call.name == "run_sql"
                    ),
                    None,
                )
                if previous_call is not None:
                    return LlmResponse(
                        tool_calls=[
                            ToolCall(
                                name=previous_call.name,
                                arguments=previous_call.arguments,
                            )
                        ]
                    )

        if attribution:
            attribution_calls = [
                ToolCall(
                    name="run_sql",
                    arguments={
                        "sql": (
                            "SELECT strftime('%Y-%m', ordered_at) AS month, "
                            "SUM(total_amount) AS revenue, COUNT(*) AS order_count, "
                            "AVG(total_amount) AS aov "
                            "FROM ecommerce.orders "
                            "WHERE status IN ('paid','completed') "
                            "GROUP BY 1 ORDER BY 1"
                        ),
                        "purpose": "确认销售额和订单量的总体变化",
                        "expected_columns": ["month", "revenue", "order_count", "aov"],
                    },
                ),
                ToolCall(
                    name="run_sql",
                    arguments={
                        "sql": (
                            "SELECT c.region, cat.name AS category, "
                            "SUM(oi.quantity * oi.unit_price) AS revenue "
                            "FROM ecommerce.orders o "
                            "JOIN ecommerce.customers c ON c.customer_id=o.customer_id "
                            "JOIN ecommerce.order_items oi ON oi.order_id=o.order_id "
                            "JOIN ecommerce.products p ON p.product_id=oi.product_id "
                            "JOIN ecommerce.categories cat ON cat.category_id=p.category_id "
                            "WHERE o.status IN ('paid','completed') "
                            "GROUP BY c.region, cat.name ORDER BY revenue DESC"
                        ),
                        "purpose": "比较地区和品类销售额贡献",
                        "expected_columns": ["region", "category", "revenue"],
                    },
                ),
                ToolCall(
                    name="run_sql",
                    arguments={
                        "sql": (
                            "SELECT o.status, COUNT(DISTINCT o.order_id) AS order_count, "
                            "SUM(o.total_amount) AS amount, "
                            "COALESCE(SUM(r.amount), 0) AS refund_amount "
                            "FROM ecommerce.orders o "
                            "LEFT JOIN ecommerce.refunds r ON r.order_id=o.order_id "
                            "GROUP BY o.status ORDER BY order_count DESC"
                        ),
                        "purpose": "检查取消与退款订单影响",
                        "expected_columns": [
                            "status",
                            "order_count",
                            "amount",
                            "refund_amount",
                        ],
                    },
                ),
            ]
            if len(sql_messages) < len(attribution_calls):
                return LlmResponse(tool_calls=[attribution_calls[len(sql_messages)]])
        elif not sql_messages:
            if "地区" in question:
                sql = (
                    "SELECT c.region, SUM(o.total_amount) AS revenue "
                    "FROM ecommerce.orders o "
                    "JOIN ecommerce.customers c ON c.customer_id=o.customer_id "
                    "WHERE o.status IN ('paid','completed') "
                    "GROUP BY c.region ORDER BY revenue DESC"
                )
                expected = ["region", "revenue"]
                purpose = "按地区统计销售额"
            elif "退款" in question:
                sql = (
                    "SELECT SUM(amount) AS refund_amount, COUNT(*) AS refund_count "
                    "FROM ecommerce.refunds"
                )
                expected = ["refund_amount", "refund_count"]
                purpose = "统计退款金额和退款次数"
            else:
                sql = (
                    "SELECT SUM(total_amount) AS revenue FROM ecommerce.orders "
                    "WHERE status IN ('paid','completed')"
                )
                expected = ["revenue"]
                purpose = "统计销售额"
            return LlmResponse(
                tool_calls=[
                    ToolCall(
                        name="run_sql",
                        arguments={
                            "sql": sql,
                            "purpose": purpose,
                            "expected_columns": expected,
                        },
                    )
                ]
            )

        if not chart_messages and ("展示" in question or "趋势" in question or attribution):
            last_sql: dict[str, Any] = next(
                (
                    json.loads(message.content)
                    for message in reversed(tool_messages)
                    if '"tool_name": "run_sql"' in message.content
                ),
                {},
            )
            data = last_sql.get("data", {})
            evidence_id = data.get("evidence_id")
            columns = data.get("columns", [])
            if evidence_id and columns:
                numeric = next(
                    (
                        column
                        for column in columns
                        if column
                        in {
                            "revenue",
                            "amount",
                            "refund_amount",
                            "order_count",
                            "refund_count",
                        }
                    ),
                    columns[-1],
                )
                category = next((column for column in columns if column != numeric), None)
                return LlmResponse(
                    tool_calls=[
                        ToolCall(
                            name="visualize_data",
                            arguments={
                                "evidence_id": evidence_id,
                                "chart_type": "line" if category == "month" else "bar",
                                "title": "经营分析结果",
                                "x": category,
                                "y": numeric,
                            },
                        )
                    ]
                )
        return LlmResponse(content="已根据工具结果完成分析。")


class OpenAICompatibleLlm(LlmService):
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 60,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.transport = transport

    async def complete(
        self,
        messages: list[LlmMessage],
        tools: list[ToolSchema],
        system_prompt: str,
    ) -> LlmResponse:
        payload_messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        for message in messages:
            item: dict[str, Any] = {"role": message.role, "content": message.content}
            if message.tool_call_id:
                item["tool_call_id"] = message.tool_call_id
            if message.tool_calls:
                item["tool_calls"] = [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(call.arguments, ensure_ascii=False),
                        },
                    }
                    for call in message.tool_calls
                ]
            payload_messages.append(item)
        payload = {
            "model": self.model,
            "messages": payload_messages,
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    },
                }
                for tool in tools
            ],
            "tool_choice": "auto",
            "temperature": 0,
            "thinking": {"type": "disabled"},
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        proxy = None if self.transport is not None else _http_proxy_from_environment()
        async with httpx.AsyncClient(
            timeout=self.timeout,
            transport=self.transport,
            proxy=proxy,
            trust_env=False,
        ) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
        body = response.json()
        choice = body["choices"][0]["message"]
        calls: list[ToolCall] = []
        for raw in choice.get("tool_calls") or []:
            function = raw["function"]
            calls.append(
                ToolCall(
                    id=raw.get("id") or str(uuid4()),
                    name=function["name"],
                    arguments=json.loads(function.get("arguments") or "{}"),
                )
            )
        usage = {
            key: int(value)
            for key, value in (body.get("usage") or {}).items()
            if isinstance(value, int)
        }
        return LlmResponse(
            content=choice.get("content"),
            tool_calls=calls,
            usage=usage,
        )
