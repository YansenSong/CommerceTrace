from pathlib import Path

from data_generator.generate import generate, load_config


def test_same_seed_produces_same_hash_and_consistent_amounts() -> None:
    config = load_config(Path(__file__).with_name("scenarios.yaml"), "test")
    first = generate(config)
    second = generate(config)

    assert first.result_hash == second.result_hash
    item_totals: dict[int, object] = {}
    for _, order_id, _, quantity, unit_price, _ in first.tables["order_items"]:
        item_totals[order_id] = item_totals.get(order_id, 0) + quantity * unit_price
    assert all(order[-1] == item_totals[order[0]] for order in first.tables["orders"])
    assert len(first.tables["refunds"]) > 0
    assert len(first.tables["inventory_snapshots"]) == config["products"] * config["inventory_days"]
    acquisition = {row[0]: row[4] for row in first.tables["customers"]}
    counts: dict[int, int] = {}
    for order in first.tables["orders"]:
        counts[order[1]] = counts.get(order[1], 0) + 1
    feed_repeat = [
        count for customer_id, count in counts.items() if acquisition[customer_id] == "信息流"
    ]
    other_repeat = [
        count for customer_id, count in counts.items() if acquisition[customer_id] != "信息流"
    ]
    assert sum(feed_repeat) / len(feed_repeat) < sum(other_repeat) / len(other_repeat)
