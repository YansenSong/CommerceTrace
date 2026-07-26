from commerce_trace.agent import Agent
from commerce_trace.context import ContextAssembler
from commerce_trace.contracts import EventType, LlmResponse, ToolCall
from commerce_trace.llm import LlmService, ScriptedLlm
from commerce_trace.memory import MemoryService
from commerce_trace.storage import InMemoryStore
from commerce_trace.tools import FakeSqlExecutor, build_default_registry


async def test_simple_question_streams_plan_tool_evidence_chart_answer_and_candidate() -> None:
    store = InMemoryStore()
    memory = MemoryService(
        store=store, schema_fingerprint="schema-v1", metric_versions={"revenue": "1"}
    )
    executor = FakeSqlExecutor(
        rows=[{"region": "华东", "revenue": 1200.0}, {"region": "华南", "revenue": 900.0}]
    )
    registry = build_default_registry(executor=executor, memory=memory)
    agent = Agent(
        llm=ScriptedLlm(),
        registry=registry,
        context_assembler=ContextAssembler(memory=memory),
        store=store,
        memory=memory,
    )

    events = [
        event
        async for event in agent.run(
            user_id="user-1",
            conversation_id="conv-1",
            request_id="req-1",
            question="按地区展示销售额",
        )
    ]

    event_types = [event.event for event in events]
    assert event_types[0] is EventType.CONVERSATION_STARTED
    assert EventType.CONTEXT_RETRIEVED in event_types
    assert EventType.PLAN_CREATED in event_types
    assert EventType.TOOL_COMPLETED in event_types
    assert EventType.EVIDENCE_CREATED in event_types
    assert EventType.CHART_CREATED in event_types
    assert event_types[-1] is EventType.ANSWER_COMPLETED
    completed = events[-1]
    assert completed.payload["evidence_ids"]
    assert "[ev_" in completed.payload["answer"]
    assert len(await store.list_memories()) == 1


async def test_retryable_sql_failure_is_corrected_within_budget() -> None:
    store = InMemoryStore()
    memory = MemoryService(store=store, schema_fingerprint="schema-v1", metric_versions={})
    executor = FakeSqlExecutor(
        rows=[{"revenue": 1000.0}],
        failures=[RuntimeError("secret database detail")],
    )
    agent = Agent(
        llm=ScriptedLlm(),
        registry=build_default_registry(executor=executor, memory=memory),
        context_assembler=ContextAssembler(memory=memory),
        store=store,
        memory=memory,
    )

    events = [
        event
        async for event in agent.run(
            user_id="user-retry",
            conversation_id="conv-retry",
            request_id="req-retry",
            question="总销售额是多少？",
        )
    ]

    assert sum(event.event is EventType.TOOL_FAILED for event in events) == 1
    completed = events[-1]
    assert completed.payload["status"] == "completed"
    assert completed.payload["usage"]["business_sql_calls"] == 2
    assert "secret database detail" not in str([event.model_dump() for event in events])


class RepeatingSqlLlm(LlmService):
    async def complete(self, messages, tools, system_prompt):  # type: ignore[no-untyped-def]
        return LlmResponse(
            tool_calls=[
                ToolCall(
                    name="run_sql",
                    arguments={
                        "sql": "SELECT COUNT(*) AS order_count FROM ecommerce.orders",
                        "purpose": "持续探索",
                        "expected_columns": ["order_count"],
                    },
                )
            ]
        )


async def test_business_sql_budget_returns_partial_evidence_and_stop_reason() -> None:
    store = InMemoryStore()
    memory = MemoryService(store=store, schema_fingerprint="schema-v1", metric_versions={})
    agent = Agent(
        llm=RepeatingSqlLlm(),
        registry=build_default_registry(
            executor=FakeSqlExecutor(rows=[{"order_count": 3}]),
            memory=memory,
        ),
        context_assembler=ContextAssembler(memory=memory),
        store=store,
        memory=memory,
        max_business_sql_calls=1,
    )

    events = [
        event
        async for event in agent.run(
            user_id="user-budget",
            conversation_id="conv-budget",
            request_id="req-budget",
            question="继续分析订单量",
        )
    ]

    completed = events[-1]
    assert completed.payload["status"] == "partial"
    assert completed.payload["stop_reason"] == "business_sql_limit"
    assert completed.payload["evidence_ids"]
    assert completed.payload["unfinished_steps"]
