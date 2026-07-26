from commerce_trace.context import ContextAssembler, schema_fingerprint


async def test_context_contains_complete_versioned_schema_before_model_use() -> None:
    context = await ContextAssembler().assemble("按地区统计销售额")

    assert len(context.schema_catalog["tables"]) == 8
    assert "ordered_at" in context.schema_catalog["tables"]["orders"]["columns"]
    assert "customers.customer_id -> orders.customer_id" in context.schema_catalog["relationships"]
    assert context.schema_fingerprint == schema_fingerprint(context.schema_catalog)
    assert context.schema_version
    assert context.knowledge_version
