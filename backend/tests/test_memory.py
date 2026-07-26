import pytest

from commerce_trace.contracts import Evidence
from commerce_trace.memory import (
    MemoryRecord,
    MemoryService,
    MemoryStatus,
    transition_memory,
)
from commerce_trace.persistence import InMemoryStore


async def test_only_final_evidence_creates_candidate_and_candidate_is_lower_rank() -> None:
    store = InMemoryStore()
    memory = MemoryService(
        store=store, schema_fingerprint="schema-v1", metric_versions={"revenue": "1"}
    )
    trusted = MemoryRecord(
        question="销售额趋势",
        analysis_step="统计销售额",
        normalized_sql="SELECT 1",
        tables_and_columns=["orders.created_at"],
        schema_fingerprint="schema-v1",
        metric_versions={"revenue": "1"},
        result_hash="trusted",
        status=MemoryStatus.TRUSTED,
    )
    await store.upsert_memory(trusted)
    evidence = Evidence(
        analysis_step="统计销售额",
        tool_call_id="tool-1",
        claim="销售额为 100 元",
        sql="SELECT SUM(total_amount) AS revenue FROM ecommerce.orders",
        columns=["revenue"],
        row_count=1,
        result_hash="candidate",
        preview=[{"revenue": 100}],
    )

    candidate = await memory.record_candidate("总销售额是多少？", evidence)
    results = await memory.search("销售额趋势", limit_candidates=2)

    assert candidate.status is MemoryStatus.CANDIDATE
    assert results[0].record.status is MemoryStatus.TRUSTED
    assert any(item.record.status is MemoryStatus.CANDIDATE for item in results)
    assert all(item.label in {"trusted", "unverified_candidate"} for item in results)


async def test_schema_change_marks_relevant_memories_stale() -> None:
    store = InMemoryStore()
    memory = MemoryService(store=store, schema_fingerprint="schema-v1", metric_versions={})
    record = MemoryRecord(
        question="订单数",
        analysis_step="计数",
        normalized_sql="SELECT COUNT(*) FROM ecommerce.orders",
        tables_and_columns=["orders"],
        schema_fingerprint="schema-v1",
        metric_versions={},
        result_hash="x",
        status=MemoryStatus.CANDIDATE,
    )
    await store.upsert_memory(record)

    changed = await memory.invalidate_versions("schema-v2", {})

    assert changed == 1
    assert (await store.list_memories())[0].status is MemoryStatus.STALE


def test_memory_state_machine_rejects_invalid_transition() -> None:
    record = MemoryRecord(
        question="订单数",
        analysis_step="计数",
        normalized_sql="SELECT COUNT(*) FROM ecommerce.orders",
        tables_and_columns=["orders"],
        schema_fingerprint="schema-v1",
        metric_versions={},
        result_hash="x",
        status=MemoryStatus.REJECTED,
    )

    with pytest.raises(ValueError, match="invalid memory transition"):
        transition_memory(record, MemoryStatus.TRUSTED)
