from __future__ import annotations

import unittest

from commerce_trace.semantic import COMMERCE_SEMANTIC_MODEL


class BusinessSemanticModelTests(unittest.TestCase):
    def test_one_model_derives_schema_policy_and_metric_context(self) -> None:
        model = COMMERCE_SEMANTIC_MODEL

        self.assertEqual(model.version, "1.0.0")
        self.assertEqual(model.schema_name, "ecommerce")
        self.assertEqual(
            model.allowed_tables,
            {
                "customers",
                "categories",
                "products",
                "orders",
                "order_items",
                "payments",
                "refunds",
                "inventory_snapshots",
            },
        )
        self.assertIn(("orders", "channel"), model.exploration_columns)
        self.assertNotIn(("customers", "name"), model.exploration_columns)

        compact = model.compact_catalog()
        self.assertEqual(compact["schema"], "ecommerce")
        self.assertNotIn("columns", compact["tables"]["orders"])
        self.assertIn(
            "customers.customer_id -> orders.customer_id",
            compact["tables"]["orders"]["relations"],
        )

        schema = model.schema_catalog()
        self.assertEqual(
            schema["tables"]["orders"]["columns"]["total_amount"],
            "real 成交总额",
        )

        revenue = model.metric("revenue")
        self.assertEqual(revenue.name, "销售额")
        self.assertEqual(revenue.filters, ("orders.status IN ('paid', 'completed')",))

    def test_fingerprint_changes_with_semantic_version(self) -> None:
        original = COMMERCE_SEMANTIC_MODEL.fingerprint()
        changed = COMMERCE_SEMANTIC_MODEL.model_copy(update={"version": "1.0.1"})

        self.assertNotEqual(original, changed.fingerprint())


if __name__ == "__main__":
    unittest.main()
