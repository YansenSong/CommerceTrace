from __future__ import annotations

import hashlib
import json
import random
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from faker import Faker

REGIONS = ["华东", "华南", "华北", "西南", "华中"]
CHANNELS = ["自然搜索", "信息流", "直播", "联盟", "老客复购"]
PAYMENT_METHODS = ["支付宝", "微信支付", "银行卡"]
CATEGORY_NAMES = ["数码", "家电", "服饰", "美妆", "食品", "家居", "运动", "母婴"]


@dataclass(frozen=True)
class GeneratedData:
    tables: dict[str, list[tuple[Any, ...]]]
    result_hash: str
    metadata: dict[str, Any]


def load_config(path: Path, profile: str) -> dict[str, Any]:
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    if profile not in config["profiles"]:
        raise ValueError(f"unknown profile: {profile}")
    return {
        "version": config["version"],
        "seed": int(config["seed"]),
        "profile": profile,
        **config["profiles"][profile],
        "scenarios": config["scenarios"],
    }


def generate(config: dict[str, Any]) -> GeneratedData:
    seed = int(config["seed"])
    rng = random.Random(seed)
    fake = Faker("zh_CN")
    fake.seed_instance(seed)
    customer_count = int(config["customers"])
    category_count = int(config["categories"])
    product_count = int(config["products"])
    order_count = int(config["orders"])

    categories = [
        (
            index,
            CATEGORY_NAMES[(index - 1) % len(CATEGORY_NAMES)] + (f"-{index}" if index > 8 else ""),
        )
        for index in range(1, category_count + 1)
    ]
    products: list[tuple[Any, ...]] = []
    for product_id in range(1, product_count + 1):
        category_id = (product_id - 1) % category_count + 1
        price = Decimal(str(round(rng.uniform(25, 2200), 2)))
        cost = (price * Decimal(str(round(rng.uniform(0.42, 0.78), 3)))).quantize(Decimal("0.01"))
        products.append(
            (
                product_id,
                category_id,
                f"{categories[category_id - 1][1]}商品{product_id}",
                price,
                cost,
            )
        )

    customers: list[tuple[Any, ...]] = []
    for customer_id in range(1, customer_count + 1):
        channel = rng.choices(CHANNELS, weights=[25, 28, 16, 8, 23], k=1)[0]
        customers.append(
            (
                customer_id,
                fake.name(),
                rng.choice(REGIONS),
                datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(days=rng.randrange(540)),
                channel,
            )
        )

    orders: list[tuple[Any, ...]] = []
    order_items: list[tuple[Any, ...]] = []
    payments: list[tuple[Any, ...]] = []
    refunds: list[tuple[Any, ...]] = []
    order_item_id = payment_id = refund_id = 0
    digital_category_ids = {
        category_id for category_id, name in categories if name.startswith("数码")
    }
    appliance_category_ids = {
        category_id for category_id, name in categories if name.startswith("家电")
    }
    product_category = {row[0]: row[1] for row in products}
    product_price = {row[0]: (row[3], row[4]) for row in products}
    feed_customer_ids = [int(row[0]) for row in customers if row[4] == "信息流"]
    other_customer_ids = [int(row[0]) for row in customers if row[4] != "信息流"]
    customer_order_counts: dict[int, int] = defaultdict(int)

    for order_id in range(1, order_count + 1):
        if feed_customer_ids and rng.random() < 0.35:
            customer_id = rng.choice(feed_customer_ids)
            if (
                customer_order_counts[customer_id] > 0
                and rng.random() > 0.12
                and other_customer_ids
            ):
                customer_id = rng.choice(other_customer_ids)
        else:
            customer_id = rng.randint(1, customer_count)
        customer_order_counts[customer_id] += 1
        customer = customers[customer_id - 1]
        ordered_at = datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(
            days=rng.randrange(273), seconds=rng.randrange(86_400)
        )
        if ordered_at.month == 7 and customer[2] == "华东" and rng.random() > 0.55:
            ordered_at = ordered_at - timedelta(days=31)
        item_count = rng.randint(1, 4)
        selected_products = rng.sample(
            range(1, product_count + 1), k=min(item_count, product_count)
        )
        total = Decimal("0")
        contains_digital = False
        contains_appliance = False
        pending_items: list[tuple[Any, ...]] = []
        for product_id in selected_products:
            quantity = rng.randint(1, 3)
            price, cost = product_price[product_id]
            total += price * quantity
            contains_digital |= product_category[product_id] in digital_category_ids
            contains_appliance |= product_category[product_id] in appliance_category_ids
            order_item_id += 1
            pending_items.append((order_item_id, order_id, product_id, quantity, price, cost))
        cancel_probability = 0.36 if contains_appliance and rng.random() < 0.32 else 0.06
        is_cancelled = rng.random() < cancel_probability
        refund_probability = 0.28 if contains_digital else 0.07
        is_refunded = not is_cancelled and rng.random() < refund_probability
        status = (
            "cancelled"
            if is_cancelled
            else "refunded"
            if is_refunded
            else rng.choice(["paid", "completed"])
        )
        total = total.quantize(Decimal("0.01"))
        orders.append((order_id, customer_id, ordered_at, status, rng.choice(CHANNELS), total))
        order_items.extend(pending_items)
        if not is_cancelled:
            payment_id += 1
            payments.append(
                (
                    payment_id,
                    order_id,
                    ordered_at + timedelta(minutes=rng.randint(1, 90)),
                    rng.choice(PAYMENT_METHODS),
                    total,
                )
            )
        if is_refunded:
            refund_id += 1
            refund_amount = (total * Decimal(str(round(rng.uniform(0.35, 1), 2)))).quantize(
                Decimal("0.01")
            )
            refunds.append(
                (
                    refund_id,
                    order_id,
                    ordered_at + timedelta(days=rng.randint(1, 20)),
                    rng.choice(["质量问题", "不喜欢", "发货延迟", "价格变化"]),
                    refund_amount,
                )
            )

    snapshots: list[tuple[Any, ...]] = []
    inventory_days = int(config["inventory_days"])
    start_day = date(2025, 7, 1)
    for day_offset in range(inventory_days):
        snapshot_day = start_day + timedelta(days=day_offset)
        for product_id in range(1, product_count + 1):
            low_stock = product_category[product_id] in appliance_category_ids
            stock = rng.randint(0, 8) if low_stock and rng.random() < 0.32 else rng.randint(20, 500)
            snapshots.append((snapshot_day, product_id, stock))

    tables = {
        "categories": categories,
        "products": products,
        "customers": customers,
        "orders": orders,
        "order_items": order_items,
        "payments": payments,
        "refunds": refunds,
        "inventory_snapshots": snapshots,
    }
    digest = hashlib.sha256()
    for table_name in sorted(tables):
        digest.update(table_name.encode())
        for row in tables[table_name]:
            digest.update(json.dumps(row, ensure_ascii=False, default=str).encode())
    result_hash = digest.hexdigest()
    return GeneratedData(
        tables=tables,
        result_hash=result_hash,
        metadata={
            "version": config["version"],
            "seed": seed,
            "profile": config["profile"],
            "result_hash": result_hash,
            "row_counts": {name: len(rows) for name, rows in tables.items()},
        },
    )


COPY_COLUMNS = {
    "categories": ("category_id", "name"),
    "products": ("product_id", "category_id", "name", "current_price", "current_cost"),
    "customers": (
        "customer_id",
        "name",
        "region",
        "registered_at",
        "acquisition_channel",
    ),
    "orders": (
        "order_id",
        "customer_id",
        "ordered_at",
        "status",
        "channel",
        "total_amount",
    ),
    "order_items": (
        "order_item_id",
        "order_id",
        "product_id",
        "quantity",
        "unit_price",
        "unit_cost",
    ),
    "payments": ("payment_id", "order_id", "paid_at", "payment_method", "amount"),
    "refunds": ("refund_id", "order_id", "refunded_at", "reason", "amount"),
    "inventory_snapshots": ("snapshot_date", "product_id", "stock_quantity"),
}
