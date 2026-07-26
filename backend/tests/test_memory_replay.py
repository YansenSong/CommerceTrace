import hashlib
import json

from commerce_trace.agent.tools import FakeSqlExecutor
from commerce_trace.memory import (
    InMemoryDerivedIndex,
    MemoryRecord,
    MemoryService,
    MemoryStatus,
)
from commerce_trace.memory.replay import GoldenCase, replay_memories
from commerce_trace.persistence import InMemoryStore
from commerce_trace.sql_safety import SqlSafetyPolicy


def result_hash(rows: list[dict[str, object]]) -> str:
    canonical = json.dumps(rows, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


async def test_replay_promotes_matching_candidate_and_is_idempotent() -> None:
    store = InMemoryStore()
    index = InMemoryDerivedIndex()
    service = MemoryService(store, "schema-v1", {"revenue": "1"}, index=index)
    sql = "SELECT SUM(total_amount) AS revenue FROM ecommerce.orders"
    candidate = MemoryRecord(
        question="销售额",
        analysis_step="统计销售额",
        normalized_sql=sql,
        tables_and_columns=["orders.total_amount"],
        schema_fingerprint="schema-v1",
        metric_versions={"revenue": "1"},
        result_hash="old",
        status=MemoryStatus.CANDIDATE,
    )
    await store.upsert_memory(candidate)
    rows: list[dict[str, object]] = [{"revenue": 100}]
    case = GoldenCase(
        case_id="revenue",
        question="销售额",
        sql=sql,
        expected_result_hash=result_hash(rows),
        schema_fingerprint="schema-v1",
        metric_versions={"revenue": "1"},
    )

    first = await replay_memories(
        service=service,
        executor=FakeSqlExecutor(rows=rows),
        policy=SqlSafetyPolicy(),
        golden_cases=[case],
    )
    second = await replay_memories(
        service=service,
        executor=FakeSqlExecutor(rows=rows),
        policy=SqlSafetyPolicy(),
        golden_cases=[case],
    )

    assert first.counts == {"passed": 1}
    assert second.counts == {"passed": 1}
    assert (await store.list_memories())[0].status is MemoryStatus.TRUSTED
    assert await index.search("销售额", 5) == [candidate.memory_id]


async def test_replay_rejects_mismatch_stales_versions_and_skips_unknown() -> None:
    store = InMemoryStore()
    service = MemoryService(store, "schema-v2", {"revenue": "1"})
    matching_sql = "SELECT COUNT(*) AS order_count FROM ecommerce.orders"
    mismatch = MemoryRecord(
        question="订单量",
        analysis_step="统计订单量",
        normalized_sql=matching_sql,
        tables_and_columns=["orders"],
        schema_fingerprint="schema-v2",
        metric_versions={"revenue": "1"},
        result_hash="old",
        status=MemoryStatus.CANDIDATE,
    )
    stale = mismatch.model_copy(
        update={
            "memory_id": "stale",
            "question": "旧销售额",
            "normalized_sql": "SELECT SUM(total_amount) FROM ecommerce.orders",
            "schema_fingerprint": "schema-v1",
        }
    )
    unknown = mismatch.model_copy(
        update={
            "memory_id": "unknown",
            "question": "未知",
            "normalized_sql": "SELECT order_id FROM ecommerce.orders",
        }
    )
    for record in (mismatch, stale, unknown):
        await store.upsert_memory(record)
    golden = GoldenCase(
        case_id="orders",
        question="订单量",
        sql=matching_sql,
        expected_result_hash="not-the-actual-hash",
        schema_fingerprint="schema-v2",
        metric_versions={"revenue": "1"},
    )

    report = await replay_memories(
        service=service,
        executor=FakeSqlExecutor(rows=[{"order_count": 12}]),
        policy=SqlSafetyPolicy(),
        golden_cases=[golden],
    )

    assert report.counts == {"rejected": 1, "stale": 1, "skipped": 1}
    statuses = {record.memory_id: record.status for record in await store.list_memories()}
    assert statuses[mismatch.memory_id] is MemoryStatus.REJECTED
    assert statuses["stale"] is MemoryStatus.STALE
    assert statuses["unknown"] is MemoryStatus.CANDIDATE
