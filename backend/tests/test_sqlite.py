from pathlib import Path

import pytest

from commerce_trace.config import Settings
from commerce_trace.contracts import EventType
from commerce_trace.llm import ScriptedLlm
from commerce_trace.memory import MemoryRecord, MemoryStatus
from commerce_trace.operations.cli import dataset_exists, generate_data, migrate
from commerce_trace.persistence import (
    SQLiteResources,
    SQLiteSchemaProvider,
    SQLiteSqlExecutor,
    SQLiteStore,
)
from commerce_trace.runtime import build_runtime


async def test_sqlite_store_opens_migrates_and_lists_memory(tmp_path: Path) -> None:
    settings = Settings(database_path=tmp_path / "commerce_trace.db")
    migrate(settings)
    resources = SQLiteResources(settings.database_path)
    await resources.open()
    try:
        store = SQLiteStore(resources)
        assert await store.list_memories() == []
        schema = await SQLiteSchemaProvider(resources).load()
        assert len(schema["tables"]) == 8

        original = MemoryRecord(
            memory_id="golden-fixed-id",
            question="销售额",
            analysis_step="统计",
            normalized_sql="SELECT 1",
            tables_and_columns=[],
            schema_fingerprint="schema-v1",
            metric_versions={"revenue": "1"},
            result_hash="old",
            status=MemoryStatus.TRUSTED,
        )
        await store.upsert_memory(original)
        updated = original.model_copy(
            update={
                "schema_fingerprint": "schema-v2",
                "result_hash": "new",
            }
        )
        saved = await store.upsert_memory(updated)
        assert saved.memory_id == "golden-fixed-id"
        assert saved.schema_fingerprint == "schema-v2"
        assert len(await store.list_memories()) == 1
    finally:
        await resources.close()


async def test_sqlite_clean_initialization_and_query_only_boundary(tmp_path: Path) -> None:
    settings = Settings(database_path=tmp_path / "commerce_trace.db")
    migrate(settings)
    metadata = generate_data(settings, "test")

    assert dataset_exists(settings)
    assert metadata["row_counts"]["orders"] > 0
    executor = SQLiteSqlExecutor(settings.database_path)
    rows = await executor.execute(
        "SELECT COUNT(*) AS order_count FROM ecommerce.orders",
        10,
    )
    assert rows[0]["order_count"] == metadata["row_counts"]["orders"]

    with pytest.raises(Exception, match="readonly|query_only|not authorized"):
        await executor.execute("DELETE FROM ecommerce.orders", 10)


async def test_sqlite_runtime_persists_real_evidence_and_replay(tmp_path: Path) -> None:
    settings = Settings(database_path=tmp_path / "commerce_trace.db")
    migrate(settings)
    generate_data(settings, "test")
    runtime = build_runtime(settings, llm=ScriptedLlm())
    for resource in runtime.resources:
        await resource.open()
    try:
        events = [
            event
            async for event in runtime.agent.run(
                user_id="sqlite-user",
                conversation_id="sqlite-conversation",
                request_id="sqlite-request",
                question="按地区展示销售额",
            )
        ]
        assert events[-1].event is EventType.ANSWER_COMPLETED
        assert events[-1].payload["status"] == "completed"
        replay = await runtime.store.replay_conversation("sqlite-user", "sqlite-conversation")
        assert replay is not None
        assert replay["evidence"]
        assert replay["tool_calls"]

        attribution_events = [
            event
            async for event in runtime.agent.run(
                user_id="sqlite-user",
                conversation_id="sqlite-attribution",
                request_id="sqlite-attribution-request",
                question="七月销售额为什么下降？",
            )
        ]
        completed = attribution_events[-1]
        assert completed.payload["status"] == "completed"
        assert len(completed.payload["evidence_ids"]) == 3
        assert any(event.event is EventType.CHART_CREATED for event in attribution_events)
    finally:
        for resource in reversed(runtime.resources):
            await resource.close()
